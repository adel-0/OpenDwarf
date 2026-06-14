"""Behavior base class — a long-running deterministic controller under a Policy.

A Behavior is to a Skill what a Skill is to a keypress: it runs for minutes of
game time, composing child Skills, and only hands control back to the LLM when
the interrupt checker (`interrupts.check`) says it must. Behaviors append factual
events to a shared `EventDigest`; on end, the loop writes one memory note from it.

`step()` mirrors `Skill.step()` and returns a `BehaviorResult`:
  RUNNING    — keep going, do not call the LLM
  DONE       — goal reached / `until` satisfied; loop ends the behavior
  NEEDS_LLM  — the behavior itself wants a decision (rare; interrupts handle most)
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from opendwarf.behaviors.digest import EventDigest
from opendwarf.behaviors.interrupts import StallWatchdog

if TYPE_CHECKING:
    from opendwarf.actions.skills import SkillContext
    from opendwarf.behaviors.policy import Policy
    from opendwarf.state.game_state import GameState


class BehaviorStatus(enum.Enum):
    RUNNING = "running"
    DONE = "done"
    NEEDS_LLM = "needs_llm"


@dataclass
class BehaviorResult:
    status: BehaviorStatus
    outcome: str = ""

    @classmethod
    def running(cls) -> "BehaviorResult":
        return cls(BehaviorStatus.RUNNING)

    @classmethod
    def done(cls, outcome: str) -> "BehaviorResult":
        return cls(BehaviorStatus.DONE, outcome)

    @classmethod
    def needs_llm(cls, reason: str) -> "BehaviorResult":
        return cls(BehaviorStatus.NEEDS_LLM, reason)


class Behavior:
    """Base class. Subclasses implement `_step()`; the public `step()` wraps it
    with watchdog bookkeeping so every behavior gets stall detection for free.
    """

    name: str = "behavior"

    def __init__(self, ctx: "SkillContext", policy: "Policy") -> None:
        self.ctx = ctx
        self.policy = policy
        self.digest = EventDigest()
        self.watchdog = StallWatchdog()

    # -- public step: watchdog wrapper -----------------------------------

    def step(self, state: "GameState") -> BehaviorResult:
        self.digest.note_tick(state.tick_counter)
        self.watchdog.observe(state, progress=self.digest.notable_count)
        return self._step(state)

    def _step(self, state: "GameState") -> BehaviorResult:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- physiology hook -------------------------------------------------

    def handles_physio(self, state: "GameState", policy: "Policy | None") -> bool:
        """Whether this behavior can resolve critical hunger/thirst/drowsiness on
        its own (so the interrupt checker should NOT fire PHYSIO_CRITICAL).
        Default: no. Behaviors that self-serve override this.
        """
        return False

    # -- announcement hook -----------------------------------------------

    def handles_announcements(self, state: "GameState") -> bool:
        """Whether this behavior pages its own routine announcements (so the
        interrupt checker should NOT fire ANNOUNCEMENT). Default: no — any
        announcement suspends to the LLM, which is right for behaviors where an
        announcement is unexpected (e.g. a patrol). Combat behaviors override
        this: every strike emits a combat-log announcement, so surrendering on
        each one would make an autopilot grind impossible (it would call the LLM
        after literally every blow). The loop still records the text for
        observability before paging it.
        """
        return False
