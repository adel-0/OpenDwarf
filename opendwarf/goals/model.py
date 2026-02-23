"""Goal data model — simplified flat goal list."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any


class GoalStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DONE = "DONE"
    DROPPED = "DROPPED"


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
