"""MemoryWriter — generates and stores memory notes on goal-revision triggers.

Significance filter (§4.2): write only if:
  - Triggered by a goal-revision event, OR
  - LLM-assigned importance ≥ 7

Importance scoring: a lightweight LLM call at write time assigns a 1–10 score
using a calibrated reference scale to prevent inflation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opendwarf.memory.model import MemoryNote
from opendwarf.memory.store import MemoryStore

if TYPE_CHECKING:
    from opendwarf.observability import EventLogger
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)

_IMPORTANCE_SYSTEM = """\
You are scoring the importance of a Dwarf Fortress observation for long-term memory.

Respond with ONLY a JSON object: {"importance": N, "reason": "<one sentence>"}

Calibration scale (how much would forgetting this hurt a future decision?):
  10 = Adventurer died — permanent lesson
  9  = First discovery of a creature weakness (e.g. wights resist slashing)
  8  = Major combat outcome with named foe; important NPC met for first time
  7  = New location discovered with strategic value
  6  = Faction standing changed; quest target identified
  5  = Found notable gear in a dungeon
  4  = Successfully navigated a difficult area
  3  = Talked to a random NPC with no quest relevance
  2  = Killed a common enemy; minor loot
  1  = DF flavor text or atmospheric description (no actionable mechanic content)

IMPORTANT: DF generates verbose flavor text like "The goblin seems annoyed by flies."
These carry NO actionable game-mechanic information — always score them 1–2.
Do NOT inflate scores out of caution. The filter is useless if everything scores 7+.
"""

# Minimum importance to store a triggered observation
_MIN_IMPORTANCE = 4


class MemoryWriter:
    """Generates memory notes from game triggers and writes them to the store."""

    def __init__(self, store: MemoryStore, llm: object, event_logger: "EventLogger | None" = None) -> None:
        self.store = store
        self.llm = llm  # LLMClient with decide(system, turn) -> dict
        self._event_logger = event_logger
        # Track accumulated importance of episodic writes this session (for reflection trigger)
        self._episodic_importance_sum: int = 0
        self._episodic_count_since_reflection: int = 0

    def on_trigger(self, trigger: str, state: "GameState") -> MemoryNote | None:
        """Generate and store a memory note for a goal-revision trigger.

        Returns the stored note, or None if significance filter rejected it.
        """
        observation, tags, entities = self._build_observation(trigger, state)
        if not observation:
            return None

        importance = self._score_importance(observation)
        if importance < _MIN_IMPORTANCE:
            logger.debug("Memory discarded (importance=%d < %d): %s", importance, _MIN_IMPORTANCE, observation[:60])
            return None

        note = MemoryNote.new(
            type=self._note_type_for(trigger),
            tick=state.tick_counter,
            importance=importance,
            tags=tags,
            content=observation,
            entities=entities,
            source="observed",
        )
        self.store.write(note)
        logger.info("Memory stored: %s [imp=%d] %s", note.id, importance, observation[:80])

        if self._event_logger:
            self._event_logger.log_memory_event(
                event="write",
                note_id=note.id,
                type=note.type,
                importance=importance,
                tags=tags,
                content_preview=observation[:100],
                trigger=trigger,
            )

        if note.type == "episodic":
            self._episodic_importance_sum += importance
            self._episodic_count_since_reflection += 1

        # Update-in-place for semantic entities
        if note.type == "semantic":
            for entity_id in entities:
                existing = self.store.find_by_entity(entity_id)
                if existing and existing.id != note.id:
                    # Merge: update content and tick, keep original ID
                    existing.content = note.content
                    existing.tick = note.tick
                    existing.importance = max(existing.importance, importance)
                    existing.tags = list(set(existing.tags) | set(tags))
                    self.store.update(existing)
                    # Remove the just-written duplicate
                    self.store.mark_expired(note)
                    logger.debug("Semantic note updated in-place: %s (entity %s)", existing.id, entity_id)
                    return existing

        return note

    def write_observation(self, content: str, tags: list[str], state: "GameState") -> MemoryNote | None:
        """Write a free-form episodic observation if it passes the significance filter."""
        importance = self._score_importance(content)
        if importance < 7:
            return None
        note = MemoryNote.new(
            type="episodic",
            tick=state.tick_counter,
            importance=importance,
            tags=tags,
            content=content,
            source="observed",
        )
        self.store.write(note)
        self._episodic_importance_sum += importance
        self._episodic_count_since_reflection += 1
        return note

    def should_reflect(self) -> bool:
        """True when the importance-sum threshold for consolidation is reached (§4.7)."""
        return self._episodic_importance_sum >= 120

    def reset_reflection_counter(self) -> None:
        self._episodic_importance_sum = 0
        self._episodic_count_since_reflection = 0

    # ------------------------------------------------------------------
    # Importance scoring
    # ------------------------------------------------------------------

    def _score_importance(self, observation: str) -> int:
        turn_prompt = f"Observation to score:\n{observation}\n\nRespond with JSON: {{\"importance\": N, \"reason\": \"...\"}}"
        try:
            result = self.llm.decide(_IMPORTANCE_SYSTEM, turn_prompt, caller="importance")
            score = int(result.get("importance", 5))
            return max(1, min(10, score))
        except Exception:
            logger.exception("Importance scoring failed; defaulting to 5")
            return 5

    # ------------------------------------------------------------------
    # Observation builders per trigger type
    # ------------------------------------------------------------------

    def _note_type_for(self, trigger: str) -> str:
        if trigger in ("location_discovered",):
            return "semantic"
        return "episodic"

    def _build_observation(
        self,
        trigger: str,
        state: "GameState",
    ) -> tuple[str, list[str], list[str]]:
        """Build (observation_text, tags, entities) for a trigger."""
        name = state.adventurer_name or "The adventurer"
        tick = state.tick_counter
        loc = state.site_name or state.region_name or "an unknown location"
        hp = state.health_pct

        if trigger == "combat_resolved":
            enemies = [u.name for u in state.hostile_units] if state.hostile_units else []
            # Use combat log for outcome clues
            log_tail = " ".join(state.combat_log[-3:]) if state.combat_log else ""
            if enemies:
                obs = (
                    f"Combat resolved at tick {tick} near {loc}. "
                    f"Fought: {', '.join(enemies)}. "
                    f"Health after combat: {hp}%. "
                )
            else:
                obs = f"Combat resolved at tick {tick} near {loc}. Health: {hp}%."
            if log_tail:
                obs += f" Recent combat: {log_tail}"
            tags = ["combat"]
            tags += ["hostile"] if hp < 50 else ["victory"]
            entities = []
            for u in (state.hostile_units or []):
                # Use type-level tag for non-historic units (no hist_fig_id in GameState)
                if u.race:
                    entities.append(f"unit_type:{u.race.upper()}")
            return obs, tags, entities

        elif trigger == "dialogue_ended":
            npcs = [r.name for r in state.npc_relationships] if state.npc_relationships else []
            if npcs:
                obs = (
                    f"Dialogue ended at tick {tick} in {loc}. "
                    f"Spoke with: {', '.join(npcs[:3])}."
                )
            else:
                obs = f"Dialogue ended at tick {tick} in {loc}."
            tags = ["npc", "dialogue"]
            entities = []
            for r in (state.npc_relationships or []):
                # NPC unit_id is transient — use name as entity key (imperfect but functional)
                if r.name:
                    entities.append(f"npc_name:{r.name}")
            return obs, tags, entities

        elif trigger.startswith("health_threshold_"):
            threshold = trigger.replace("health_threshold_", "")
            obs = (
                f"{name} health dropped below {threshold}% at tick {tick} in {loc}. "
                f"Current HP: {hp}%. "
            )
            if state.in_combat and state.hostile_units:
                foes = ", ".join(u.name for u in state.hostile_units[:3])
                obs += f"Engaged with: {foes}."
            tags = ["danger", "health", "combat" if state.in_combat else "injury"]
            return obs, tags, []

        elif trigger == "location_discovered":
            site = state.site_name or state.region_name or "new area"
            site_type = state.site_type or "unknown type"
            obs = (
                f"Discovered location: {site} ({site_type}) at tick {tick}. "
                f"Position: {state.adventurer_position}."
            )
            tags = ["location", "site", "exploration"]
            return obs, tags, []

        elif trigger == "dialogue_forced":
            obs = (
                f"Unexpected dialogue triggered at tick {tick} in {loc}. "
                f"Current menu: {state.menu_state}."
            )
            tags = ["npc", "dialogue", "unexpected"]
            return obs, tags, []

        # Other triggers (session_start, wait_long) — no observation
        return "", [], []
