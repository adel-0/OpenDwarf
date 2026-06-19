"""Autotelic learning-progress curriculum — the breadth engine for the Director.

The GoalManager keeps a short *tactical* goal list (≤3, revised on events). On its
own that produces survival, not *breadth*: nothing pushes the agent to develop new
families of capability (combat, social, exploration, wealth, knowledge, …). Hand-
enumerating the wiki is a losing game (RESEARCH.md); the principled alternative,
converged on by the 2026 open-ended-agent literature, is an **autotelic curriculum
driven by learning progress**:

- MAGELLAN (arXiv:2502.07709): in a large goal space, choose what to practice next
  in proportion to *learning progress* (recent competence change) — the "zone of
  proximal development" — not by difficulty and not at random. This escapes both
  the already-mastered (LP→0) and the currently-impossible (LP→0) regions.
- LMA3 (arXiv:2305.12487): let the LLM *propose* diverse, concrete, world-grounded
  goals; breadth comes from generation, not enumeration.
- AEL (arXiv:2604.21725) "less is more": do NOT bolt on a heavy competence-modeling
  subsystem. Reuse signals already in the loop and the existing goal-revision call.

So this module does only two cheap things, both pure-Python and LLM-free:

1. `CompetenceLedger` — a persisted record of per-capability competence (0..1) and
   its history, fed from signals the loop already produces (combat skill levels,
   goal completions, exploration/social triggers).
2. `select_focus()` — pick the capability *dimension* to emphasise next, by absolute
   learning progress + an optimism bonus for under-practised dimensions.

The chosen dimension is injected as a one-line hint into the existing
`GoalManager.revise_and_plan` prompt, where the LLM (LMA3 step) turns it into a
concrete goal grounded in the current world. No new LLM call is added.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class Capability(str, Enum):
    """Breadth dimensions an adventurer can develop. Deliberately small — these are
    *families* (one per Phase-5 capability cluster), not individual skills. The LLM
    proposes concrete goals within the chosen family."""

    COMBAT = "combat"           # fighting, weapon/dodge skills
    EXPLORATION = "exploration"  # mapping, reaching new sites/z-levels
    SOCIAL = "social"           # conversation, rumors, recruiting, reputation
    SURVIVAL = "survival"       # eat/drink/sleep/flee, staying alive in the wild
    WEALTH = "wealth"           # loot, trade, equipment upgrades
    KNOWLEDGE = "knowledge"     # books/slabs, secrets, quest lore
    RENOWN = "renown"           # performances, fame, becoming known


# DF skill ids that count toward a capability's competence (max level among them is
# used as the competence proxy). Capabilities without a clear skill signal (wealth,
# knowledge, renown) are driven purely by goal completions / triggers.
_COMBAT_SKILLS = frozenset({
    "MELEE_COMBAT", "AXE", "SWORD", "MACE", "HAMMER", "SPEAR", "DAGGER",
    "WRESTLING", "STRIKING", "BITE", "DODGING", "ARMOR", "SHIELD",
    "BIWEAPON", "GRASP_STRIKE", "MISC_WEAPON",
})
_SOCIAL_SKILLS = frozenset({
    "CONVERSATION", "PERSUASION", "NEGOTIATION", "INTIMIDATION", "FLATTERY",
    "CONSOLE", "PACIFY", "COMEDY", "LYING", "SOCIAL", "JUDGING_INTENT",
})
_RENOWN_SKILLS = frozenset({
    "SING_MUSIC", "PLAY_KEYBOARD_INSTRUMENT", "POETRY", "DANCE",
    "MAKE_MUSIC", "WRITING", "PROSE", "RHETORIC",
})

# DF skill levels run 0 (Dabbling) .. 15 (Legendary); divide by this to normalise.
_LEGENDARY_LEVEL = 15.0

# Map a capability to the skill set that proxies its competence.
_CAPABILITY_SKILLS: dict[Capability, frozenset[str]] = {
    Capability.COMBAT: _COMBAT_SKILLS,
    Capability.SOCIAL: _SOCIAL_SKILLS,
    Capability.RENOWN: _RENOWN_SKILLS,
}

# Selection weights (module constants — tune, don't parameterise per call).
_W_LEARNING_PROGRESS = 1.0   # favour dimensions where competence is moving
_W_EXPLORE = 0.35            # optimism for under-practised dimensions
_W_MASTERY_PENALTY = 0.25    # avoid grinding an already-mastered, flat dimension

# How many competence samples define the "recent" vs "older" window for LP.
_LP_WINDOW = 3
# Cap on stored history samples per capability (keeps competence.json bounded).
_MAX_HISTORY = 24


@dataclass
class _CapState:
    competence: float = 0.0
    attempts: int = 0
    last_focus_tick: int = 0
    history: list[tuple[int, float]] = field(default_factory=list)  # (tick, competence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "competence": round(self.competence, 4),
            "attempts": self.attempts,
            "last_focus_tick": self.last_focus_tick,
            "history": [[t, round(c, 4)] for t, c in self.history[-_MAX_HISTORY:]],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "_CapState":
        return cls(
            competence=float(d.get("competence", 0.0)),
            attempts=int(d.get("attempts", 0)),
            last_focus_tick=int(d.get("last_focus_tick", 0)),
            history=[(int(t), float(c)) for t, c in d.get("history", [])],
        )


class CompetenceLedger:
    """Per-capability competence + history, persisted to goals/competence.json.

    Competence is a monotone-ish 0..1 estimate. It is *observed* (set to the max of
    its current value and a new sample) rather than decayed — capability, once gained,
    does not erode within a life. Learning progress is the recent *change* in that
    estimate, which is what the curriculum optimises for.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._caps: dict[Capability, _CapState] = {c: _CapState() for c in Capability}
        if path is not None and path.exists():
            self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
            for name, sd in data.get("capabilities", {}).items():
                try:
                    cap = Capability(name)
                except ValueError:
                    continue
                self._caps[cap] = _CapState.from_dict(sd)
            logger.info("Loaded competence ledger from %s", self.path)
        except Exception:
            logger.exception("Failed to load competence ledger; starting fresh")

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"capabilities": {c.value: st.to_dict() for c, st in self._caps.items()}}
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # -- observation -------------------------------------------------------

    def observe(self, cap: Capability, competence: float, tick: int) -> None:
        """Record a competence sample. Competence is taken as the max of the prior
        estimate and the sample (capability does not erode), but the *sample* is what
        we log to history so learning progress reflects real change."""
        st = self._caps[cap]
        competence = max(0.0, min(1.0, competence))
        new_comp = max(st.competence, competence)
        # Only append a history point when the estimate actually moves, so flat
        # dimensions read as LP≈0 rather than accumulating identical samples.
        if not st.history or abs(new_comp - st.history[-1][1]) > 1e-6:
            st.history.append((tick, new_comp))
            if len(st.history) > _MAX_HISTORY:
                st.history = st.history[-_MAX_HISTORY:]
        st.competence = new_comp

    def bump(self, cap: Capability, delta: float, tick: int) -> None:
        """Nudge competence up by `delta` from a discrete achievement (goal done,
        rumor learned). Diminishing returns near mastery."""
        st = self._caps[cap]
        headroom = 1.0 - st.competence
        self.observe(cap, st.competence + delta * headroom, tick)

    def mark_focus(self, cap: Capability, tick: int) -> None:
        st = self._caps[cap]
        st.attempts += 1
        st.last_focus_tick = tick

    # -- queries -----------------------------------------------------------

    def competence(self, cap: Capability) -> float:
        return self._caps[cap].competence

    def attempts(self, cap: Capability) -> int:
        return self._caps[cap].attempts

    def learning_progress(self, cap: Capability) -> float:
        """Absolute learning progress: |mean(recent samples) - mean(older samples)|.

        Absolute (MAGELLAN/ALP) so the signal is "how fast is competence *moving*",
        which peaks in the zone of proximal development and is ~0 both for mastered
        and for not-yet-started dimensions. Returns 0.0 when there is too little
        history to measure."""
        hist = self._caps[cap].history
        if len(hist) < 2:
            return 0.0
        comps = [c for _, c in hist]
        recent = comps[-_LP_WINDOW:]
        older = comps[-2 * _LP_WINDOW:-_LP_WINDOW] or comps[:-len(recent)]
        if not older:
            return 0.0
        return abs(_mean(recent) - _mean(older))

    def snapshot(self) -> dict[Capability, dict[str, float]]:
        """Per-capability {competence, learning_progress, attempts} for logging."""
        return {
            c: {
                "competence": round(self.competence(c), 3),
                "learning_progress": round(self.learning_progress(c), 3),
                "attempts": self.attempts(c),
            }
            for c in Capability
        }

    # -- bulk observation from game signals --------------------------------

    def observe_from_skills(self, skills: list[Any], tick: int) -> None:
        """Update skill-backed capabilities from the adventurer's skill list.

        `skills` is GameState.skills — objects with `.id` (str) and `.level` (int)."""
        for cap, skill_ids in _CAPABILITY_SKILLS.items():
            best = 0
            for sk in skills:
                if getattr(sk, "id", None) in skill_ids:
                    best = max(best, int(getattr(sk, "level", 0)))
            if best > 0:
                self.observe(cap, min(best / _LEGENDARY_LEVEL, 1.0), tick)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


# Goal-revision triggers that count as evidence of progress on a capability, and how
# big a bump each is worth. Used by the GoalManager to feed the ledger cheaply.
TRIGGER_CAPABILITY_BUMPS: dict[str, tuple[Capability, float]] = {
    "combat_resolved": (Capability.COMBAT, 0.05),
    "new_location": (Capability.EXPLORATION, 0.06),
    "dialogue_ended": (Capability.SOCIAL, 0.04),
}


def select_focus(
    ledger: CompetenceLedger,
    candidates: list[Capability] | None = None,
    *,
    rng: Any | None = None,
    epsilon: float = 0.0,
) -> Capability:
    """Choose the capability dimension to emphasise next.

    score = w_lp·learning_progress + w_explore·optimism − w_mastery·mastery_penalty

    - learning_progress (MAGELLAN): favour the zone of proximal development.
    - optimism = 1/(attempts+1): try under-practised dimensions (cold-start breadth).
    - mastery_penalty = competence when LP≈0: stop grinding a flat, mastered family.

    Deterministic argmax by default (tie-break: fewest attempts, then enum order).
    Pass `rng` + `epsilon` for ε-greedy exploration (kept out of the default path so
    behaviour is reproducible and testable)."""
    cands = candidates or list(Capability)
    if not cands:
        raise ValueError("select_focus needs at least one candidate capability")

    if rng is not None and epsilon > 0.0 and rng.random() < epsilon:
        return rng.choice(cands)

    def score(cap: Capability) -> tuple[float, int, int]:
        lp = ledger.learning_progress(cap)
        attempts = ledger.attempts(cap)
        comp = ledger.competence(cap)
        optimism = 1.0 / (attempts + 1)
        mastery_penalty = comp if lp < 1e-3 else 0.0
        s = (
            _W_LEARNING_PROGRESS * lp
            + _W_EXPLORE * optimism
            - _W_MASTERY_PENALTY * mastery_penalty
        )
        # Tie-breakers: prefer fewer attempts, then taxonomy order (stable).
        return (s, -attempts, -list(Capability).index(cap))

    return max(cands, key=score)
