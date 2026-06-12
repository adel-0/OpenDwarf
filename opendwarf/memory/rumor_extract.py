"""Rumor extraction — NORTHSTAR M3 step 2 (ROADMAP 3.3).

A cheap LLM pass over a finished conversation transcript that pulls out *places
the NPC told the adventurer about* — directions to sites, whereabouts of
figures, named locations worth travelling to. Each candidate is matched against
the live world-site list (`LuaExecutor.resolve_site`) to obtain a concrete world
position + site id, then folded into the `SiteRegistry` so the agent can later
`journey:<rumor_id>` there.

This closes the quest loop: hear a rumor in conversation → registry entry with a
position → `journey` → arrive. The extraction is deliberately a separate, cheap
caller (`caller="rumor_extract"`) so it can run on a small/fast model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_RUMOR_SYSTEM = """\
You extract TRAVEL DESTINATIONS from a Dwarf Fortress adventure-mode conversation.

You are given a transcript. Lines prefixed "NPC:" are what the NPC said; lines
prefixed "YOU:" are what the adventurer said. Pull out named PLACES the NPC
revealed that the adventurer could travel to — e.g. "directions to <site>", a
fortress/town/cave/tower/camp the NPC mentioned, the whereabouts of a figure
tied to a place. Use the NPC's words only; ignore places the adventurer named.

For each destination give the place's NAME exactly as said (so it can be matched
against the world map), a short TYPE if stated or obvious (town, fortress, cave,
tower, camp, hamlet, dark fortress, ...), and a one-clause NOTE on why it matters
(who/what is there). Do NOT invent places. If the NPC gave no real destination,
return an empty list.

Respond with ONLY a JSON object:
{"sites": [{"name": "<place name>", "type": "<type or empty>", "note": "<why>"}]}
"""


@dataclass
class RumorCandidate:
    name: str
    site_type: str = ""
    note: str = ""


class RumorExtractor:
    """Runs the extraction LLM call and resolves candidates to world positions."""

    def __init__(self, llm: object, lua: object) -> None:
        self.llm = llm
        self.lua = lua

    def extract(self, transcript: str) -> list[RumorCandidate]:
        """Parse a transcript into rumored-destination candidates. Returns [] when
        the transcript is empty or the model finds nothing (never raises)."""
        if not transcript or not transcript.strip():
            return []
        from opendwarf.llm.base import PromptBundle
        try:
            result = self.llm.decide(
                PromptBundle.simple(_RUMOR_SYSTEM, f"Transcript:\n{transcript}"),
                caller="rumor_extract",
            )
        except Exception:
            logger.exception("Rumor extraction LLM call failed")
            return []
        out: list[RumorCandidate] = []
        for raw in (result or {}).get("sites", []) or []:
            if not isinstance(raw, dict):
                continue
            name = (raw.get("name") or "").strip()
            if not name:
                continue
            out.append(RumorCandidate(
                name=name,
                site_type=(raw.get("type") or "").strip(),
                note=(raw.get("note") or "").strip(),
            ))
        return out

    def resolve(self, cand: RumorCandidate) -> dict | None:
        """Match a candidate name to a real world site. Returns the nearest match
        dict (id, name, type, world_x, world_y, distance) or None if unresolved."""
        try:
            matches = self.lua.resolve_site(cand.name)
        except Exception:
            logger.exception("resolve_site failed for rumor %r", cand.name)
            return None
        return matches[0] if matches else None

    def harvest(self, transcript: str, registry, *, tick: int = 0) -> int:
        """Full pass: extract candidates, resolve each, and fold into the registry.
        Returns the number of registry entries written/updated."""
        n = 0
        for cand in self.extract(transcript):
            match = self.resolve(cand)
            if match is not None:
                registry.record_rumor(
                    name=match.get("name") or cand.name,
                    site_type=cand.site_type or match.get("type", ""),
                    notes=cand.note,
                    tick=tick,
                    site_id=match.get("id"),
                    world_x=match.get("world_x"),
                    world_y=match.get("world_y"),
                )
            else:
                # Heard of it but can't place it on the map — record name-only so
                # the agent at least remembers the lead.
                registry.record_rumor(
                    name=cand.name, site_type=cand.site_type, notes=cand.note, tick=tick,
                )
            n += 1
        return n
