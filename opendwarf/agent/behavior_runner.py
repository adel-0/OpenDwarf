"""Behavior (autopilot) lifecycle management for the tactical loop.

``BehaviorRunner`` owns the active/suspended behavior slots, runs per-tick
interrupt checking, and dispatches the five autopilot intents the LLM can
choose (patrol, grind_combat, journey, resume, abort_behavior).

It receives shared infrastructure (extractor, policy, site_registry,
memory_writer, skill_ctx) via constructor injection and communicates
side-effects back to ``TacticalLoop`` through three narrow callbacks:
- ``set_last_state(None)``  — tell the loop to re-extract state next tick
- ``execute_key(key)``      — send a DFHack input
- ``record_announcements(state)`` — capture announcement lines to the buffer
- ``append_history(line)``  — push a line to the loop's history deque
- ``log_event(**fields)``   — write a structured log entry
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

from opendwarf.behaviors import interrupts as interrupts_mod
from opendwarf.behaviors.base import BehaviorStatus
from opendwarf.behaviors.grind_combat import GrindCombatBehavior
from opendwarf.behaviors.interrupts import Interrupt
from opendwarf.behaviors.journey import JourneyBehavior
from opendwarf.behaviors.patrol import PatrolBehavior

if TYPE_CHECKING:
    from opendwarf.actions.skills import SkillContext
    from opendwarf.behaviors.base import Behavior
    from opendwarf.behaviors.policy import Policy
    from opendwarf.memory.writer import MemoryWriter
    from opendwarf.spatial.extractor import MapExtractor
    from opendwarf.spatial.sites import SiteRegistry
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)


class BehaviorRunner:
    """Manages autopilot behavior lifecycle for the tactical loop.

    The loop delegates three responsibilities here:
    1. Behavior tick — interrupt check, step, sleep, state-invalidation.
    2. Autopilot intent dispatch — patrol/grind_combat/journey/resume/abort.
    3. Autopilot prompt blocks — action-line additions and status block.
    """

    def __init__(
        self,
        skill_ctx: "SkillContext",
        policy: "Policy",
        site_registry: "SiteRegistry",
        extractor: "MapExtractor",
        memory_writer: "MemoryWriter | None",
        set_last_state: Callable,        # set_last_state(None) to force re-extract
        execute_key: Callable[[str], None],
        record_announcements: Callable,  # record_announcements(state)
        append_history: Callable[[str], None],
        log_event: Callable,             # log_event(event, **fields)
    ) -> None:
        self._skill_ctx = skill_ctx
        self.policy = policy
        self._site_registry = site_registry
        self._extractor = extractor
        self._memory_writer = memory_writer
        self._set_last_state = set_last_state
        self._execute_key = execute_key
        self._record_announcements = record_announcements
        self._append_history = append_history
        self._log_event = log_event

        # Behavior slots — the loop reads these directly via properties.
        self._active_behavior: "Behavior | None" = None
        self._suspended_behavior: "Behavior | None" = None
        self._interrupt: "Interrupt | None" = None

    # ------------------------------------------------------------------
    # Public read-only properties (loop reads these to build prompt blocks
    # and decide whether to run a behavior tick vs the normal LLM path)
    # ------------------------------------------------------------------

    @property
    def active(self) -> "Behavior | None":
        return self._active_behavior

    @property
    def suspended(self) -> "Behavior | None":
        return self._suspended_behavior

    @property
    def interrupt(self) -> "Interrupt | None":
        return self._interrupt

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def run_tick(self, state: "GameState") -> None:
        """Step the active behavior for one loop tick.

        Runs the interrupt check first; suspends on interrupt, steps on
        clear. Handles routine behavior-paged announcements inline. The
        loop returns immediately after calling this — all side-effects
        (sleep, last_state invalidation) happen inside.
        """
        behavior = self._active_behavior
        self._extractor.ensure_fresh(state)

        intr = interrupts_mod.check(state, self.policy, behavior)
        if intr is not None:
            self._suspend_behavior(intr)
            return

        # Interrupt check cleared us, but a routine announcement (combat log) may
        # be up that the behavior opted to page itself (handles_announcements).
        # Record it for observability, dismiss it, and stay on autopilot — the
        # behavior can't act while the announcement viewer blocks input anyway.
        if state.showing_announcements:
            self._record_announcements(state)
            self._execute_key("SELECT")
            self._set_last_state(None)
            time.sleep(0.3)
            return

        result = behavior.step(state)
        if result.status is BehaviorStatus.RUNNING:
            self._set_last_state(None)
            time.sleep(0.35)
            return
        if result.status is BehaviorStatus.NEEDS_LLM:
            self._suspend_behavior(
                Interrupt(interrupts_mod.InterruptReason.STALLED, result.outcome))
            return
        # DONE
        self._end_behavior(state, result.outcome, ended=True)

    # ------------------------------------------------------------------
    # Autopilot intent dispatch (called from loop._handle_autopilot_action)
    # ------------------------------------------------------------------

    def handle_action(
        self, base: str, action: str, state: "GameState"
    ) -> bool:
        """Intercept autopilot control intents before normal dispatch.

        Returns True if the action was handled here, False if it was not
        an autopilot intent (caller then falls through to normal dispatch).
        """
        if base not in ("patrol", "resume", "abort_behavior", "grind_combat", "journey"):
            return False

        if base == "resume" and self._suspended_behavior is not None:
            self._active_behavior = self._suspended_behavior
            self._suspended_behavior = None
            self._interrupt = None
            self._append_history(f"resumed {self._active_behavior.name} autopilot")
            self._set_last_state(None)
            return True

        if base == "abort_behavior":
            self._end_behavior(state, "aborted by LLM", ended=False)
            return True

        if base == "patrol":
            radius = 8
            if ":" in action:
                try:
                    radius = max(2, int(action.split(":", 1)[1].strip()))
                except ValueError:
                    pass
            self._active_behavior = PatrolBehavior(self._skill_ctx, self.policy, radius=radius)
            self._suspended_behavior = None
            self._interrupt = None
            logger.info("Started PatrolBehavior (radius %d)", radius)
            self._append_history(f"started patrol autopilot (radius {radius})")
            self._set_last_state(None)
            return True

        if base == "grind_combat":
            radius, until = self._parse_grind_args(action)
            self._active_behavior = GrindCombatBehavior(
                self._skill_ctx, self.policy, radius=radius, until=until)
            self._suspended_behavior = None
            self._interrupt = None
            logger.info("Started GrindCombatBehavior (radius %d, until %s)", radius, until)
            self._append_history(f"started grind_combat autopilot (radius {radius})")
            self._set_last_state(None)
            return True

        if base == "journey":
            arg = action.split(":", 1)[1].strip() if ":" in action else ""
            site_id, site_name, world_pos = self._resolve_journey_dest(arg, state)
            if site_id is None and not site_name and world_pos is None:
                self._append_history(f"journey: no destination site found for {arg!r}")
                self._set_last_state(None)
                return True
            self._active_behavior = JourneyBehavior(
                self._skill_ctx, self.policy, site_id=site_id, site_name=site_name,
                world_pos=world_pos)
            self._suspended_behavior = None
            self._interrupt = None
            label = site_name or f"site {site_id}"
            logger.info("Started JourneyBehavior toward %s", label)
            self._append_history(f"started journey autopilot toward {label}")
            self._set_last_state(None)
            return True

        # `resume` with nothing suspended — treat as no-op handled action.
        self._append_history(f"{base}: no suspended behavior")
        self._set_last_state(None)
        return True

    # ------------------------------------------------------------------
    # Prompt block builders (called from loop._tick prompt section)
    # ------------------------------------------------------------------

    def autopilot_action_lines(self, state: "GameState") -> str:
        """Return the autopilot section appended to the action block.

        Always offers patrol; offers resume/abort_behavior only when a
        behavior is suspended.
        """
        from opendwarf.agent.loop import _normal_play_focus  # avoid circular at import time
        lines: list[str] = []
        if self._suspended_behavior is not None:
            name = self._suspended_behavior.name
            lines.append(f"  resume — continue the suspended {name} autopilot")
            lines.append(f"  abort_behavior — discard the suspended {name} autopilot")
        elif _normal_play_focus(state):
            lines.append("  patrol — auto-walk a loop around here unattended (handles food/water "
                         "per policy; hands back on combat/dialogue/low health). Optional radius: patrol:12")
            if self.policy.engage_species_allow or self.policy.engage_tier_max:
                lines.append("  grind_combat — hunt & fight policy-authorized hostiles near here to "
                             "train combat skills, eating/drinking per policy; hands back on "
                             "unauthorized/excess hostiles or low health. Optional radius and stop "
                             "condition: grind_combat:12 or grind_combat:12:AXE:8 (stop at AXE lv8)")
            else:
                lines.append("  (grind_combat unavailable: set policy.engage_species_allow or "
                             "policy.engage_tier_max first so the autopilot knows what it may fight)")
            distant = [s for s in state.nearby_sites
                       if s.distance and s.distance > 2 and s.name != state.site_name]
            if distant:
                ex = distant[0]
                lines.append(
                    "  journey:<site_id> — travel across the world to a distant site, "
                    "routing around terrain barriers and re-entering travel after each "
                    "interruption; hands back on encounters/critical needs. "
                    f"e.g. journey:{ex.id} ({ex.name}, {ex.distance} tiles {ex.direction})")
            rumor_block = self._site_registry.format_for_prompt()
            if rumor_block:
                lines.append(rumor_block)
        if not lines:
            return ""
        return "\nAutopilot (runs without further LLM turns until interrupted):\n" + "\n".join(lines)

    def autopilot_status_block(self) -> str:
        """Return the autopilot status block (interrupt summary + digest)."""
        if self._interrupt is None or self._suspended_behavior is None:
            return ""
        behavior = self._suspended_behavior
        return (f"-- Autopilot interrupted: {self._interrupt} --\n"
                + behavior.digest.render(behavior_name=behavior.name)
                + "\nChoose `resume` to continue it, `abort_behavior` to drop it, or any other action "
                  "(the behavior stays parked and `resume` re-arms it).")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _suspend_behavior(self, intr: "Interrupt") -> None:
        """Park the active behavior (keep it) and surface the interrupt + digest
        to the next LLM turn."""
        behavior = self._active_behavior
        logger.info("Behavior %s suspended: %s", behavior.name, intr)
        self._suspended_behavior = behavior
        self._active_behavior = None
        self._interrupt = intr
        self._log_event("behavior_suspended", reason=str(intr),
                        digest=behavior.digest.one_line(behavior_name=behavior.name))
        self._set_last_state(None)

    def _end_behavior(self, state: "GameState", outcome: str, *, ended: bool) -> None:
        """Terminate a behavior (DONE or aborted): record digest to history, write
        one episodic memory note, and clear the slot."""
        behavior = self._active_behavior or self._suspended_behavior
        if behavior is None:
            return
        one_line = behavior.digest.one_line(behavior_name=behavior.name)
        self._append_history(f"{one_line} — {outcome}")
        logger.info("Behavior %s ended: %s", behavior.name, outcome)
        self._log_event("behavior_ended", reason=outcome, digest=one_line)
        if self._memory_writer is not None and not behavior.digest.is_empty:
            try:
                self._memory_writer.write_observation(
                    f"Autopilot {behavior.name}: {one_line}. Outcome: {outcome}.",
                    tags=["autopilot", behavior.name], state=state)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to write behavior memory note")
        self._active_behavior = None
        self._suspended_behavior = None
        self._interrupt = None
        self._set_last_state(None)

    @staticmethod
    def _parse_grind_args(action: str) -> tuple[int, dict]:
        """Parse grind_combat[:radius[:SKILL:level]] | grind_combat:radius:max_ticks:N.

        Returns (radius, until_dict). Malformed segments fall back to defaults so a
        bad LLM intent never crashes the turn.
        """
        radius, until = 12, {}
        parts = action.split(":")[1:]  # drop the "grind_combat" head
        if parts and parts[0].strip():
            try:
                radius = max(4, int(parts[0]))
            except ValueError:
                pass
        if len(parts) >= 3:
            key = parts[1].strip()
            try:
                until[key] = int(parts[2])
            except ValueError:
                pass
        return radius, until

    def _resolve_journey_dest(
        self, arg: str, state: "GameState"
    ) -> tuple[int | None, str, tuple[int, int] | None]:
        """Resolve a journey argument to (site_id, site_name, world_pos)."""
        if not arg:
            return None, "", None

        if arg.lstrip("-").isdigit():
            sid = int(arg)
            for s in state.nearby_sites:
                if s.id == sid:
                    return sid, s.name, None
        else:
            low = arg.lower()
            for s in state.nearby_sites:
                if s.name.lower() == low:
                    return s.id, s.name, None
            for s in state.nearby_sites:
                if low in s.name.lower():
                    return s.id, s.name, None

        entry = self._site_registry.get(arg)
        if entry is not None:
            return entry.site_id, entry.name, entry.world_pos

        if arg.lstrip("-").isdigit():
            return int(arg), "", None
        return None, "", None
