"""Unit tests for the SiteRegistry + RumorExtractor (M3 site registry / rumor glue)."""

from __future__ import annotations

from opendwarf.memory.rumor_extract import RumorCandidate, RumorExtractor
from opendwarf.spatial.sites import SiteRegistry, _slug


# ----------------------------------------------------------------------
# SiteRegistry
# ----------------------------------------------------------------------

def test_record_observed_and_get_by_id_and_name():
    reg = SiteRegistry()
    reg.record_observed(site_id=7, name="Ironhold", site_type="fortress",
                        world_x=120, world_y=80, tick=10)
    e = reg.get("7")
    assert e is not None and e.name == "Ironhold"
    assert e.world_pos == (120, 80)
    assert e.confidence == 1.0 and e.source == "observed"
    # resolvable by name and by name-substring too
    assert reg.get("ironhold") is e
    assert reg.get("iron") is e


def test_rumor_with_resolved_position_is_journeyable():
    reg = SiteRegistry()
    e = reg.record_rumor(name="Speardread", site_type="dark fortress",
                         notes="goblin lair", tick=5, site_id=42,
                         world_x=300, world_y=10)
    assert e.has_pos and e.world_pos == (300, 10)
    assert e.source == "rumor" and 0.5 < e.confidence < 1.0
    assert reg.get("42") is e


def test_name_only_rumor_has_no_position():
    reg = SiteRegistry()
    e = reg.record_rumor(name="The Lost Hamlet", notes="someone fled there", tick=1)
    assert not e.has_pos
    assert e.confidence < 0.5
    assert e.key == _slug("The Lost Hamlet")
    # journey hint should mark it unreachable
    assert "cannot travel yet" in reg.format_for_prompt()


def test_observed_upgrades_prior_name_only_rumor():
    reg = SiteRegistry()
    reg.record_rumor(name="Ironhold", notes="a great fortress", tick=1)
    assert reg.get("ironhold").source == "rumor"
    # Later we actually see it in the nearby-site list.
    reg.record_observed(site_id=7, name="Ironhold", site_type="fortress",
                        world_x=120, world_y=80, tick=20)
    e = reg.get("ironhold")
    assert e.source == "observed" and e.site_id == 7 and e.world_pos == (120, 80)
    # No duplicate entry left behind.
    assert len([x for x in reg.rumors()]) == 0
    assert len(reg) == 1


def test_rumor_does_not_downgrade_observed():
    reg = SiteRegistry()
    reg.record_observed(site_id=7, name="Ironhold", world_x=1, world_y=2, tick=1)
    reg.record_rumor(name="Ironhold", notes="rumor note", tick=5, site_id=7)
    e = reg.get("7")
    assert e.source == "observed" and e.confidence == 1.0
    assert e.notes == "rumor note"  # notes still refreshed


def test_rumors_sorted_by_confidence():
    reg = SiteRegistry()
    reg.record_rumor(name="Faraway", tick=1)                       # name-only, low
    reg.record_rumor(name="Placed", tick=2, site_id=9, world_x=5, world_y=5)  # resolved, high
    rumors = reg.rumors()
    assert rumors[0].name == "Placed"


def test_persistence_roundtrip(tmp_path):
    p = tmp_path / "sites.json"
    reg = SiteRegistry(p)
    reg.record_observed(site_id=7, name="Ironhold", world_x=120, world_y=80, tick=10)
    reg.record_rumor(name="Speardread", tick=5, site_id=42, world_x=300, world_y=10)
    reg.save()

    reloaded = SiteRegistry.load(p)
    assert len(reloaded) == 2
    assert reloaded.get("7").world_pos == (120, 80)
    assert reloaded.get("42").name == "Speardread"


# ----------------------------------------------------------------------
# RumorExtractor
# ----------------------------------------------------------------------

class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.callers: list[str] = []

    def decide(self, bundle, *, caller="tactical"):
        self.callers.append(caller)
        return self.payload


class _FakeLua:
    def __init__(self, matches_by_query=None):
        self.matches_by_query = matches_by_query or {}
        self.queries: list[str] = []

    def resolve_site(self, name):
        self.queries.append(name)
        return self.matches_by_query.get(name, [])


def test_extract_parses_sites_and_uses_rumor_caller():
    llm = _FakeLLM({"sites": [
        {"name": "Speardread", "type": "dark fortress", "note": "goblin lair"},
        {"name": "", "type": "x", "note": "skip me"},  # empty name dropped
    ]})
    ex = RumorExtractor(llm, _FakeLua())
    cands = ex.extract("NPC: Seek Speardread to the north.")
    assert [c.name for c in cands] == ["Speardread"]
    assert llm.callers == ["rumor_extract"]


def test_extract_empty_transcript_skips_llm():
    llm = _FakeLLM({"sites": []})
    ex = RumorExtractor(llm, _FakeLua())
    assert ex.extract("   ") == []
    assert llm.callers == []


def test_harvest_resolves_and_records_position():
    llm = _FakeLLM({"sites": [{"name": "Speardread", "type": "fortress", "note": "lair"}]})
    lua = _FakeLua({"Speardread": [
        {"id": 42, "name": "Speardread", "type": "dark fortress",
         "world_x": 300, "world_y": 10, "distance": 150},
    ]})
    reg = SiteRegistry()
    ex = RumorExtractor(llm, lua)
    n = ex.harvest("NPC: Speardread lies north.", reg, tick=7)
    assert n == 1
    e = reg.get("42")
    assert e is not None and e.world_pos == (300, 10)
    assert e.notes == "lair"


def test_harvest_unresolved_records_name_only():
    llm = _FakeLLM({"sites": [{"name": "Nowheresville", "type": "", "note": "vague"}]})
    lua = _FakeLua({})  # no match
    reg = SiteRegistry()
    ex = RumorExtractor(llm, lua)
    n = ex.harvest("NPC: I heard of Nowheresville once.", reg, tick=3)
    assert n == 1
    e = reg.get("Nowheresville")
    assert e is not None and not e.has_pos
