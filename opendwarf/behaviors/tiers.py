"""Creature danger tiers — the starter table that lets a Policy authorize fights
by difficulty class instead of enumerating every race string.

Sourced from `memory/df_mechanics.md` "Creature Danger Tiers" (1=weak .. 4=severe).
This is deliberately a small, hand-curated seed; the flywheel grows it from
combat outcomes. An unknown race is treated as tier 3 (dangerous) so the
autopilot errs toward handing back to the LLM rather than charging a mystery.
"""

from __future__ import annotations

DEFAULT_TIER = 3  # unknown race ⇒ dangerous (conservative: interrupt, don't auto-engage)

# Race string (upper-case) → danger tier. Race strings come from DF's creature
# raw ids (state.UnitInfo.race), which are upper-case tokens like "GOBLIN".
_RACE_TIER: dict[str, int] = {
    # Tier 1 — weak: kill quickly, low XP, safe training fodder
    "KOBOLD": 1,
    "RAT": 1,
    "GIANT_RAT": 1,
    "RACCOON": 1,
    "GROUNDHOG": 1,
    "SPARROW": 1,
    "BIRD_SPARROW": 1,
    # Tier 2 — moderate: can kill an unprepared adventurer; check HP first
    "GOBLIN": 2,
    "HUMAN": 2,        # bandits/outlaws are usually human/goblin/dwarf
    "DWARF": 2,
    "ELF": 2,
    "WOLF": 2,
    "TROLL": 2,
    "BLACK_BEAR": 2,
    "GRIZZLY_BEAR": 2,
    "LEOPARD": 2,
    "BOAR": 2,
    # Tier 3 — dangerous: fight carefully or avoid
    "OGRE": 3,
    "GIANT_CAVE_SPIDER": 3,  # webbing — flee per demons.md guidance
    "GIANT": 3,
    "MINOTAUR": 3,
    "CYCLOPS": 3,
    "ELEPHANT": 3,
    # Tier 4 — severe: do not engage unless very well equipped
    "DRAGON": 4,
    "ROC": 4,
    "FORGOTTEN_BEAST": 4,
    "NIGHT_TROLL": 4,
    "HYDRA": 4,
    "BRONZE_COLOSSUS": 4,
    "TITAN": 4,
    "DEMON": 4,
}


def tier_of(race: str | None) -> int:
    """Danger tier for a creature race string (case-insensitive). Unknown ⇒ DEFAULT_TIER."""
    if not race:
        return DEFAULT_TIER
    return _RACE_TIER.get(race.upper(), DEFAULT_TIER)
