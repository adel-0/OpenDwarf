"""Policy — a structured standing order written/revised by the LLM, executed by code.

The LLM stops being the actor and becomes the author of the actor: behaviors and
the interrupt checker consult the Policy to decide what runs without an LLM turn.
v0 is deliberately small (NORTHSTAR II.1) — extend only when a behavior needs it.

Persisted at goals/policy.json. The LLM revises it via an optional "policy" key
in the decision JSON (same pattern as "scratchpad"): known fields are validated
and applied, unknown keys ignored, and the diff is logged by the caller.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Policy:
    """Standing orders for autopilot behaviors. v0 deliberately small."""

    engage_species_allow: list[str] = field(default_factory=list)  # races the autopilot MAY fight
    engage_tier_max: int = 0          # also engage any race at/below this danger tier (0 = off; 1..4)
    max_opponents: int = 1            # engage only if hostiles <= this
    min_health_pct: int = 60          # engage only if health >= this
    flee_below_health_pct: int = 40   # autopilot flees without asking
    eat_when_hungry: bool = True
    drink_when_thirsty: bool = True
    sleep_indoors_only: bool = True
    never: list[str] = field(default_factory=list)  # free-text hard rules, shown to Tactician

    # ------------------------------------------------------------------
    # Engagement authorization
    # ------------------------------------------------------------------

    def allows_engaging(self, race: str | None) -> bool:
        """True iff this policy sanctions fighting `race` — on the species
        allow-list, or at/below the engage tier ceiling. Shared by the interrupt
        checker (is the current danger authorized?) and GrindCombatBehavior
        (which wild creatures may I proactively hunt?)."""
        from opendwarf.behaviors.tiers import tier_of

        if (race or "").upper() in {s.upper() for s in self.engage_species_allow}:
            return True
        return self.engage_tier_max > 0 and tier_of(race) <= self.engage_tier_max

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def to_prompt_line(self) -> str:
        engage = ", ".join(self.engage_species_allow) if self.engage_species_allow else "nothing"
        if self.engage_tier_max:
            engage += f" + any creature at tier <= {self.engage_tier_max}"
        parts = [
            f"engage: {engage} (max {self.max_opponents} opponent{'s' if self.max_opponents != 1 else ''}, health >= {self.min_health_pct}%)",
            f"flee below {self.flee_below_health_pct}% health",
        ]
        physio = []
        if self.eat_when_hungry:
            physio.append("eat when hungry")
        if self.drink_when_thirsty:
            physio.append("drink when thirsty")
        physio.append("sleep indoors only" if self.sleep_indoors_only else "sleep anywhere")
        parts.append("; ".join(physio))
        if self.never:
            parts.append("never: " + ", ".join(self.never))
        return "Policy (autopilot standing orders): " + " | ".join(parts)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Policy":
        policy = cls()
        policy.revise(d)
        return policy

    @classmethod
    def load(cls, path: Path) -> "Policy":
        """Load from JSON; defaults if the file is missing or unreadable."""
        if not path.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            logger.exception("Failed to load policy from %s; using defaults", path)
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # LLM revision
    # ------------------------------------------------------------------

    def revise(self, updates: dict[str, Any]) -> dict[str, list[Any]]:
        """Apply a partial update from the LLM. Returns {field: [old, new]} for
        fields actually changed. Unknown keys and type-invalid values are ignored
        (logged), never raised — a malformed revision must not kill the turn.
        """
        diff: dict[str, list[Any]] = {}
        if not isinstance(updates, dict):
            logger.warning("Policy revision is not an object: %r", updates)
            return diff
        valid = {f.name: f for f in fields(self)}
        for key, value in updates.items():
            f = valid.get(key)
            if f is None:
                logger.warning("Policy revision: unknown field %r ignored", key)
                continue
            coerced = self._coerce(key, value)
            if coerced is None:
                logger.warning("Policy revision: invalid value for %s: %r", key, value)
                continue
            old = getattr(self, key)
            if coerced != old:
                setattr(self, key, coerced)
                diff[key] = [old, coerced]
        return diff

    @staticmethod
    def _coerce(key: str, value: Any) -> Any | None:
        """Validate/coerce one field value; None means reject."""
        if key in ("engage_species_allow", "never"):
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                return value
            return None
        if key in ("max_opponents", "min_health_pct", "flee_below_health_pct", "engage_tier_max"):
            # bool is an int subclass — reject it explicitly
            if isinstance(value, bool) or not isinstance(value, int):
                return None
            if key == "max_opponents":
                return value if 0 <= value <= 10 else None
            if key == "engage_tier_max":
                return value if 0 <= value <= 4 else None
            return value if 0 <= value <= 100 else None
        if key in ("eat_when_hungry", "drink_when_thirsty", "sleep_indoors_only"):
            return value if isinstance(value, bool) else None
        return None
