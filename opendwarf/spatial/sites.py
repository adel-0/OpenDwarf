"""Site registry — spatial-memory Layer 3 (ROADMAP 3.2/3.3, NORTHSTAR M3 step 2).

Knowledge about *places* that may have no explored tiles yet: sites the
adventurer has seen in the nearby-site list, and sites it has only *heard of*
in conversation (rumors). Each entry carries a world-tile centre position when
one is known (exact for observed sites, name-resolved for rumors) plus a
confidence and provenance.

The registry is the resolution table for the `journey:<rumor_id>` intent: a
rumor id maps to a `SiteEntry`, which gives `JourneyBehavior` either a known
`site_id` (steer by the nearby-site bearing) or a `world_pos` (steer by absolute
bearing) to travel to.

Coordinates are DF *embark-tile* (global) coordinates — the same space as
`site.global_min/max_*` and `region_x/y + local//16`, NOT army coords (which are
3× these). `NearbySite.world_x/world_y` and `state.player_world_x/y` are in this
space.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Confidence anchors.
_CONF_OBSERVED = 1.0      # seen in the live nearby-site list — ground truth
_CONF_RESOLVED = 0.85     # rumor whose name resolved to a real world site
_CONF_RUMOR = 0.4         # rumor with no position yet (name only)


def _slug(name: str) -> str:
    """Stable key for a name-only rumor (lowercased, alnum-joined)."""
    return "r-" + re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


@dataclass
class SiteEntry:
    key: str                      # stable rumor_id used in journey:<rumor_id>
    name: str
    site_type: str = ""
    site_id: int | None = None    # world site id, when known
    world_x: int | None = None    # embark-tile centre
    world_y: int | None = None
    confidence: float = _CONF_RUMOR
    source: str = "rumor"         # "observed" | "rumor"
    notes: str = ""
    last_tick: int = 0

    @property
    def has_pos(self) -> bool:
        return self.world_x is not None and self.world_y is not None

    @property
    def world_pos(self) -> tuple[int, int] | None:
        return (self.world_x, self.world_y) if self.has_pos else None


class SiteRegistry:
    """Persistent map of known/rumored sites, keyed by a stable rumor id."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._entries: dict[str, SiteEntry] = {}
        # Secondary index: world site_id -> key, so observed sweeps and rumor
        # resolution collapse onto the same entry.
        self._by_site_id: dict[int, str] = {}

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, rumor_id: str) -> SiteEntry | None:
        """Resolve a journey argument to an entry: by key, by numeric site_id,
        or by a name slug / name substring."""
        if not rumor_id:
            return None
        if rumor_id in self._entries:
            return self._entries[rumor_id]
        if rumor_id.lstrip("-").isdigit():
            k = self._by_site_id.get(int(rumor_id))
            if k:
                return self._entries[k]
        slug = _slug(rumor_id)
        if slug in self._entries:
            return self._entries[slug]
        low = rumor_id.strip().lower()
        for e in self._entries.values():
            if e.name.lower() == low:
                return e
        for e in self._entries.values():
            if low and low in e.name.lower():
                return e
        return None

    def rumors(self) -> list[SiteEntry]:
        """Entries the agent has only heard about (not currently in view), most
        confident first — the candidates for `journey:<rumor_id>`."""
        out = [e for e in self._entries.values() if e.source == "rumor"]
        out.sort(key=lambda e: (e.confidence, e.last_tick), reverse=True)
        return out

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_observed(
        self,
        *,
        site_id: int,
        name: str,
        site_type: str = "",
        world_x: int | None = None,
        world_y: int | None = None,
        tick: int = 0,
    ) -> SiteEntry:
        """Fold a site from the live nearby-site list into the registry. Observed
        data is ground truth: it upgrades an existing rumor in place (adopting the
        position + site_id and flipping source to 'observed')."""
        key = self._by_site_id.get(site_id)
        if key is None and name:
            # A prior name-only rumor for the same place — adopt it.
            slug = _slug(name)
            if slug in self._entries and self._entries[slug].site_id is None:
                key = slug
        if key is None:
            key = str(site_id)
        e = self._entries.get(key)
        if e is None:
            e = SiteEntry(key=key, name=name, site_type=site_type, site_id=site_id)
            self._entries[key] = e
        e.name = name or e.name
        e.site_type = site_type or e.site_type
        e.site_id = site_id
        if world_x is not None and world_y is not None:
            e.world_x, e.world_y = world_x, world_y
        e.confidence = _CONF_OBSERVED
        e.source = "observed"
        e.last_tick = tick
        self._by_site_id[site_id] = key
        return e

    def record_rumor(
        self,
        *,
        name: str,
        site_type: str = "",
        notes: str = "",
        tick: int = 0,
        site_id: int | None = None,
        world_x: int | None = None,
        world_y: int | None = None,
    ) -> SiteEntry | None:
        """Record a site heard about in conversation. When `site_id`/`world_*`
        are supplied (the rumor name resolved to a real world site) the entry is
        high-confidence and journey-able; otherwise it is a name-only marker.

        Never downgrades an already-observed entry."""
        if not name.strip():
            return None
        # Collapse onto an existing entry: by resolved site_id first, then slug.
        key = None
        if site_id is not None:
            key = self._by_site_id.get(site_id)
        slug = _slug(name)
        if key is None:
            key = slug if slug in self._entries else None
        if key is None:
            key = str(site_id) if site_id is not None else slug
        e = self._entries.get(key)
        if e is None:
            e = SiteEntry(key=key, name=name, site_type=site_type)
            self._entries[key] = e

        if e.source == "observed":
            # Don't clobber ground truth; just refresh notes/tick.
            if notes:
                e.notes = notes
            e.last_tick = tick
            return e

        e.name = name or e.name
        e.site_type = site_type or e.site_type
        if notes:
            e.notes = notes
        if site_id is not None:
            e.site_id = site_id
            self._by_site_id[site_id] = key
        if world_x is not None and world_y is not None:
            e.world_x, e.world_y = world_x, world_y
        e.confidence = _CONF_RESOLVED if e.has_pos else _CONF_RUMOR
        e.source = "rumor"
        e.last_tick = tick
        return e

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def format_for_prompt(self, *, limit: int = 5) -> str:
        """A compact block of rumored, journey-able destinations for the LLM."""
        rumors = [e for e in self.rumors()][:limit]
        if not rumors:
            return ""
        lines = ["-- Rumored sites (journey:<id> to travel there) --"]
        for e in rumors:
            pos = "" if e.has_pos else " (location unknown — cannot travel yet)"
            stype = f" {e.site_type}" if e.site_type else ""
            note = f" — {e.notes}" if e.notes else ""
            lines.append(f"  journey:{e.key} → {e.name}{stype}{pos}{note}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path | None = None) -> None:
        path = path or self.path
        if path is None:
            return
        data = {"entries": [asdict(e) for e in self._entries.values()]}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        logger.debug("Saved site registry (%d entries) to %s", len(self._entries), path)

    @classmethod
    def load(cls, path: Path) -> "SiteRegistry":
        reg = cls(path)
        if not path.exists():
            return reg
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for raw in data.get("entries", []):
                e = SiteEntry(**{k: raw.get(k) for k in SiteEntry.__dataclass_fields__})
                reg._entries[e.key] = e
                if e.site_id is not None:
                    reg._by_site_id[e.site_id] = e.key
            logger.info("Loaded site registry: %d entries from %s", len(reg._entries), path)
        except Exception:
            logger.exception("Failed to load site registry %s; starting fresh", path)
        return reg
