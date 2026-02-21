"""Goal data model for the Layer 3 goal management system."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GoalType(str, Enum):
    SURVIVAL = "SURVIVAL"
    PHYSIOLOGICAL = "PHYSIOLOGICAL"
    SOCIAL = "SOCIAL"
    EXPLORATION = "EXPLORATION"
    RENOWN = "RENOWN"
    NARRATIVE = "NARRATIVE"


class GoalStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    ACHIEVED = "ACHIEVED"
    DROPPED = "DROPPED"
    FAILED = "FAILED"


@dataclass
class Goal:
    """A structured goal record with full lifecycle tracking."""

    id: str
    description: str
    type: GoalType
    status: GoalStatus
    priority: float  # 0.0–1.0
    created_tick: int

    parent_id: str | None = None
    sub_goal_ids: list[str] = field(default_factory=list)

    # Optional targeting metadata
    target_hf_id: int | None = None           # resolved hist_fig id for person goals
    acquisition_method: str | None = None     # "LOOT" | "BUY" | "TAKE"
    exploration_budget: int | None = None     # ticks before auto-fail; None = unlimited
    success_condition: dict[str, Any] | None = None  # structured check dict

    # Lifecycle metadata
    fail_reason: str | None = None
    notes: str = ""

    @classmethod
    def new(
        cls,
        description: str,
        type: GoalType,
        priority: float,
        created_tick: int,
        *,
        status: GoalStatus = GoalStatus.CANDIDATE,
        parent_id: str | None = None,
        **kwargs: Any,
    ) -> "Goal":
        return cls(
            id=str(uuid.uuid4())[:8],
            description=description,
            type=type,
            status=status,
            priority=priority,
            created_tick=created_tick,
            parent_id=parent_id,
            **kwargs,
        )

    def is_terminal(self) -> bool:
        return self.status in (GoalStatus.ACHIEVED, GoalStatus.DROPPED, GoalStatus.FAILED)

    def is_active(self) -> bool:
        return self.status == GoalStatus.ACTIVE

    def is_candidate(self) -> bool:
        return self.status == GoalStatus.CANDIDATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "type": self.type.value,
            "status": self.status.value,
            "priority": self.priority,
            "created_tick": self.created_tick,
            "parent_id": self.parent_id,
            "sub_goal_ids": self.sub_goal_ids,
            "target_hf_id": self.target_hf_id,
            "acquisition_method": self.acquisition_method,
            "exploration_budget": self.exploration_budget,
            "success_condition": self.success_condition,
            "fail_reason": self.fail_reason,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Goal":
        return cls(
            id=d["id"],
            description=d["description"],
            type=GoalType(d["type"]),
            status=GoalStatus(d["status"]),
            priority=d["priority"],
            created_tick=d["created_tick"],
            parent_id=d.get("parent_id"),
            sub_goal_ids=d.get("sub_goal_ids", []),
            target_hf_id=d.get("target_hf_id"),
            acquisition_method=d.get("acquisition_method"),
            exploration_budget=d.get("exploration_budget"),
            success_condition=d.get("success_condition"),
            fail_reason=d.get("fail_reason"),
            notes=d.get("notes", ""),
        )

    def summary_line(self) -> str:
        return f"[{self.status.value}|{self.type.value}|p={self.priority:.2f}] {self.description}"
