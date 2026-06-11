"""Tests for the autopilot Policy (NORTHSTAR II.1)."""

from pathlib import Path

from opendwarf.behaviors.policy import Policy


def test_defaults():
    p = Policy()
    assert p.engage_species_allow == []
    assert p.max_opponents == 1
    assert p.min_health_pct == 60
    assert p.flee_below_health_pct == 40
    assert p.eat_when_hungry and p.drink_when_thirsty and p.sleep_indoors_only
    assert p.never == []


def test_json_round_trip(tmp_path: Path):
    p = Policy(
        engage_species_allow=["WOLF", "BANDIT"],
        max_opponents=2,
        min_health_pct=70,
        flee_below_health_pct=35,
        sleep_indoors_only=False,
        never=["fight_in_water"],
    )
    path = tmp_path / "policy.json"
    p.save(path)
    loaded = Policy.load(path)
    assert loaded == p


def test_load_missing_file_returns_defaults(tmp_path: Path):
    assert Policy.load(tmp_path / "nope.json") == Policy()


def test_load_corrupt_file_returns_defaults(tmp_path: Path):
    path = tmp_path / "policy.json"
    path.write_text("{not json", encoding="utf-8")
    assert Policy.load(path) == Policy()


def test_revise_applies_valid_fields_and_returns_diff():
    p = Policy()
    diff = p.revise({"engage_species_allow": ["WOLF"], "flee_below_health_pct": 50})
    assert p.engage_species_allow == ["WOLF"]
    assert p.flee_below_health_pct == 50
    assert diff == {
        "engage_species_allow": [[], ["WOLF"]],
        "flee_below_health_pct": [40, 50],
    }


def test_revise_ignores_unknown_keys():
    p = Policy()
    diff = p.revise({"bogus_field": 1, "max_opponents": 3})
    assert not hasattr(p, "bogus_field")
    assert diff == {"max_opponents": [1, 3]}


def test_revise_rejects_invalid_values():
    p = Policy()
    diff = p.revise({
        "max_opponents": "two",          # wrong type
        "min_health_pct": 150,           # out of range
        "flee_below_health_pct": True,   # bool is not an int here
        "eat_when_hungry": "yes",        # wrong type
        "never": ["ok", 42],             # non-string element
    })
    assert diff == {}
    assert p == Policy()


def test_revise_non_dict_is_noop():
    p = Policy()
    assert p.revise("be careful") == {}  # type: ignore[arg-type]
    assert p == Policy()


def test_revise_unchanged_value_not_in_diff():
    p = Policy()
    assert p.revise({"max_opponents": 1}) == {}


def test_from_dict_ignores_unknown_and_invalid():
    p = Policy.from_dict({"max_opponents": 4, "junk": True, "min_health_pct": "high"})
    assert p.max_opponents == 4
    assert p.min_health_pct == 60


def test_to_prompt_line():
    line = Policy(engage_species_allow=["WOLF"], never=["steal_in_sites"]).to_prompt_line()
    assert "WOLF" in line
    assert "flee below 40%" in line
    assert "never: steal_in_sites" in line
    assert "\n" not in line
