"""Action registry: data-driven availability, prompt generation, and dispatch.

Each ActionSpec describes one category of action. The registry uses them to (a)
render the per-turn action list for the LLM and (b) resolve a chosen action
string into an executable Dispatch. Adding a capability = adding one ActionSpec.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from opendwarf.actions.skills import (
    FastTravelController,
    FleeSkill,
    MenuSkill,
    QuestLogSkill,
    RouteExecutor,
    Skill,
    SkillContext,
    SleepSkill,
    TalkToSkill,
    _MenuStep,
)
from opendwarf.spatial.chunk_map import Cell

if TYPE_CHECKING:
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)


class ActionKind(enum.Enum):
    KEY = "key"       # single deferred input
    SKILL = "skill"   # multi-tick controller
    CONTEXT = "context"  # conversation choice


@dataclass
class Dispatch:
    kind: ActionKind
    canonical: str
    key: str | None = None
    skill: Skill | None = None
    conv_index: int | None = None
    error: str | None = None


@dataclass
class ActionSpec:
    name: str
    kind: ActionKind
    group: str
    available: Callable[["GameState"], bool]
    enumerate_fn: Callable[["GameState"], list[tuple[str, str]]]
    matches: Callable[[str], bool]
    make: Callable[[str, "GameState", SkillContext], Dispatch]


# Prompt grouping order
_GROUP_ORDER = ["movement", "travel", "combat", "conversation", "item", "other"]
_GROUP_HEADER = {
    "movement": "Movement & navigation (pathfinding handles walls automatically):",
    "travel": "Long-distance travel:",
    "combat": "Combat:",
    "conversation": "Conversation:",
    "item": "Items:",
    "other": "Other:",
}

_DIRS8 = {
    "n": (0, -1), "s": (0, 1), "e": (1, 0), "w": (-1, 0),
    "ne": (1, -1), "nw": (-1, -1), "se": (1, 1), "sw": (-1, 1),
}


# ----------------------------------------------------------------------
# Helpers shared by spec definitions
# ----------------------------------------------------------------------

def _in_conversation(s: "GameState") -> bool:
    return s.conversation_phase != "none"


def _normal_play(s: "GameState") -> bool:
    """Free exploration: not in a menu, not travelling, not fighting."""
    return not _in_conversation(s) and not s.fast_travel_active and not s.hostile_units


def _key_dispatch(canonical: str, key: str) -> Callable[[str, "GameState", SkillContext], Dispatch]:
    return lambda action, state, ctx: Dispatch(ActionKind.KEY, canonical, key=key)


def _find_nearest_stair(ctx: SkillContext, state: "GameState", up: bool):
    """Search the chunk map near the adventurer for the closest stair tile."""
    center = ctx.extractor.adventurer_abs(state)
    if center is None:
        return None
    cx, cy, cz = center
    want = {Cell.STAIR_UP, Cell.STAIR_UPDOWN} if up else {Cell.STAIR_DOWN, Cell.STAIR_UPDOWN}
    best, best_d = None, 1 << 30
    for r in range(1, 41):
        for y in range(cy - r, cy + r + 1):
            for x in range(cx - r, cx + r + 1):
                if max(abs(x - cx), abs(y - cy)) != r:
                    continue
                if ctx.chunk_map.get(x, y, cz) in want:
                    d = abs(x - cx) + abs(y - cy)
                    if d < best_d:
                        best, best_d = (x, y, cz), d
        if best is not None:
            break
    return best


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------

class ActionRegistry:
    def __init__(self, specs: list[ActionSpec]):
        self._specs = specs

    def build_block(self, state: "GameState", banned: set[str] | None = None) -> str:
        banned = banned or set()
        groups: dict[str, list[str]] = {g: [] for g in _GROUP_ORDER}
        for spec in self._specs:
            if not spec.available(state):
                continue
            for action_str, desc in spec.enumerate_fn(state):
                if action_str in banned:
                    continue
                line = f"  {action_str}" + (f" — {desc}" if desc else "")
                groups.setdefault(spec.group, []).append(line)

        lines = ["--- Available Actions ---"]
        for g in _GROUP_ORDER:
            if groups.get(g):
                lines.append(_GROUP_HEADER.get(g, g + ":"))
                lines.extend(groups[g])
        return "\n".join(lines)

    def resolve(self, action: str, state: "GameState", ctx: SkillContext) -> Dispatch:
        for spec in self._specs:
            if spec.matches(action):
                try:
                    return spec.make(action, state, ctx)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Action %r failed to resolve: %s", action, exc)
                    return Dispatch(ActionKind.KEY, "wait", key="A_MOVE_SAME_SQUARE", error=str(exc))
        logger.warning("Unknown action %r — defaulting to wait", action)
        return Dispatch(ActionKind.KEY, "wait", key="A_MOVE_SAME_SQUARE", error="unknown action")


# ----------------------------------------------------------------------
# Default spec set
# ----------------------------------------------------------------------

def default_registry() -> ActionRegistry:
    specs: list[ActionSpec] = []

    # --- single-step movement keys (combat / precise positioning) ---
    move_keys = {f"move_{d}": f"A_MOVE_{d.upper()}" for d in _DIRS8}
    specs.append(ActionSpec(
        name="move", kind=ActionKind.KEY, group="movement",
        available=lambda s: _normal_play(s) or bool(s.hostile_units),
        enumerate_fn=lambda s: [(a, None) for a in move_keys],
        matches=lambda a: a in move_keys,
        make=lambda a, s, c: Dispatch(ActionKind.KEY, a, key=move_keys[a]),
    ))

    # --- pathfinding to a unit ---
    def enum_goto_unit(s: "GameState"):
        out = []
        for u in s.nearby_units:
            if not u.is_hostile and u.distance > 1:
                out.append((f"goto_unit:{u.id}", f"path to {u.name} ({u.race}, dist={u.distance})"))
        return out[:8]

    def make_goto_unit(a, s, c):
        uid = int(a.split(":", 1)[1])
        name = next((u.name for u in s.nearby_units if u.id == uid), f"unit {uid}")
        skill = RouteExecutor(c, target_unit_id=uid, label=name)
        return Dispatch(ActionKind.SKILL, a, skill=skill)

    specs.append(ActionSpec(
        name="goto_unit", kind=ActionKind.SKILL, group="movement",
        available=lambda s: _normal_play(s) and bool(enum_goto_unit(s)),
        enumerate_fn=enum_goto_unit,
        matches=lambda a: a.startswith("goto_unit:"),
        make=make_goto_unit,
    ))

    # --- frontier exploration ---
    specs.append(ActionSpec(
        name="explore", kind=ActionKind.SKILL, group="movement",
        available=_normal_play,
        enumerate_fn=lambda s: [(f"explore:{d}", None) for d in _DIRS8],
        matches=lambda a: a.startswith("explore:"),
        make=lambda a, s, c: Dispatch(
            ActionKind.SKILL, a,
            skill=RouteExecutor(c, frontier_dir=_DIRS8.get(a.split(":", 1)[1], (0, -1)),
                                label=f"explore {a.split(':', 1)[1]}"),
        ),
    ))

    # --- goto_pos (explicit coordinate) ---
    def make_goto_pos(a, s, c):
        x, y, z = (int(v) for v in a.split(":", 1)[1].split(","))
        return Dispatch(ActionKind.SKILL, a, skill=RouteExecutor(c, goal=(x, y, z), label=f"({x},{y},{z})"))

    specs.append(ActionSpec(
        name="goto_pos", kind=ActionKind.SKILL, group="movement",
        available=lambda s: False,  # not advertised; usable if a plan emits it
        enumerate_fn=lambda s: [],
        matches=lambda a: a.startswith("goto_pos:"),
        make=make_goto_pos,
    ))

    # --- goto_stairs ---
    def make_goto_stairs(a, s, c):
        up = a.split(":", 1)[1] == "up"
        target = _find_nearest_stair(c, s, up)
        if target is None:
            return Dispatch(ActionKind.KEY, "wait", key="A_MOVE_SAME_SQUARE",
                            error="no known stairs")
        return Dispatch(ActionKind.SKILL, a,
                        skill=RouteExecutor(c, goal=target, label=f"stairs {a.split(':', 1)[1]}"))

    specs.append(ActionSpec(
        name="goto_stairs", kind=ActionKind.SKILL, group="movement",
        available=_normal_play,
        enumerate_fn=lambda s: [("goto_stairs:up", "path to nearest up-stair"),
                                ("goto_stairs:down", "path to nearest down-stair")],
        matches=lambda a: a.startswith("goto_stairs:"),
        make=make_goto_stairs,
    ))

    # --- fast travel to a site ---
    def enum_goto_site(s: "GameState"):
        out = []
        for site in s.nearby_sites:
            if s.site_name and site.name == s.site_name:
                continue
            if site.distance == 0:
                continue
            out.append((f"goto_site:{site.id}",
                        f"fast-travel to {site.name} ({site.site_type}), {site.distance} tiles {site.direction}"))
        return out

    def make_goto_site(a, s, c):
        sid = int(a.split(":", 1)[1])
        site = next((x for x in s.nearby_sites if x.id == sid), None)
        return Dispatch(ActionKind.SKILL, a,
                        skill=FastTravelController(c, site_id=sid, site_name=site.name if site else ""))

    specs.append(ActionSpec(
        name="goto_site", kind=ActionKind.SKILL, group="travel",
        available=lambda s: _normal_play(s) and bool(enum_goto_site(s)),
        enumerate_fn=enum_goto_site,
        matches=lambda a: a.startswith("goto_site:"),
        make=make_goto_site,
    ))

    # --- combat / waiting / interaction key actions ---
    specs.append(ActionSpec(
        name="attack", kind=ActionKind.KEY, group="combat",
        available=lambda s: bool(s.hostile_units) or s.in_combat,
        enumerate_fn=lambda s: [("attack", "attack adjacent hostile (opens target selection in v50+)")],
        matches=lambda a: a == "attack",
        make=_key_dispatch("attack", "A_ATTACK"),
    ))
    specs.append(ActionSpec(
        name="flee", kind=ActionKind.SKILL, group="combat",
        available=lambda s: bool(s.hostile_units),
        enumerate_fn=lambda s: [("flee", "flee from all hostiles — routes away, stops when safe (15+ tiles)")],
        matches=lambda a: a == "flee",
        make=lambda a, s, c: Dispatch(ActionKind.SKILL, a, skill=FleeSkill(c)),
    ))
    specs.append(ActionSpec(
        name="yield", kind=ActionKind.KEY, group="combat",
        available=lambda s: bool(s.hostile_units),
        enumerate_fn=lambda s: [("yield", "yield/surrender to hostile (may stop combat)")],
        matches=lambda a: a == "yield",
        make=_key_dispatch("yield", "A_YIELD"),
    ))
    specs.append(ActionSpec(
        name="talk", kind=ActionKind.KEY, group="other",
        available=_normal_play,
        enumerate_fn=lambda s: [("talk", "initiate conversation with a nearby NPC")],
        matches=lambda a: a == "talk",
        make=_key_dispatch("talk", "A_TALK"),
    ))

    # --- talk_to:<unit_id> — re-engage a specific NPC (for multi-turn conversations) ---
    def _enum_talk_to(s: "GameState"):
        out = []
        for u in s.nearby_units:
            if not u.is_hostile and u.hist_fig_id >= 0 and u.distance <= 4:
                out.append((f"talk_to:{u.id}",
                            f"start conversation with {u.name} ({u.race}) — auto-selects in NPC list"))
        return out[:6]

    def _make_talk_to(a, s, c):
        uid = int(a.split(":", 1)[1])
        unit = next((u for u in s.nearby_units if u.id == uid), None)
        name = unit.name if unit else f"unit {uid}"
        return Dispatch(ActionKind.SKILL, a, skill=TalkToSkill(c, unit_id=uid, npc_name=name))

    specs.append(ActionSpec(
        name="talk_to", kind=ActionKind.SKILL, group="conversation",
        available=lambda s: _normal_play(s) and bool(_enum_talk_to(s)),
        enumerate_fn=_enum_talk_to,
        matches=lambda a: a.startswith("talk_to:"),
        make=_make_talk_to,
    ))
    specs.append(ActionSpec(
        name="wait", kind=ActionKind.KEY, group="other",
        available=lambda s: not _in_conversation(s),
        enumerate_fn=lambda s: [("wait", "wait in place (1 instant)"),
                                ("wait_long", "wait/rest (10 instants)")],
        matches=lambda a: a in ("wait", "wait_long"),
        make=lambda a, s, c: Dispatch(ActionKind.KEY, a,
                                      key="A_MOVE_SAME_SQUARE" if a == "wait" else "A_WAIT"),
    ))
    specs.append(ActionSpec(
        name="sleep", kind=ActionKind.SKILL, group="other",
        available=_normal_play,
        enumerate_fn=lambda s: [("sleep", "sleep until dawn (safe location only — bogeymen outdoors at night)")],
        matches=lambda a: a == "sleep",
        make=lambda a, s, c: Dispatch(ActionKind.SKILL, a, skill=SleepSkill(c)),
    ))

    # --- eat / drink ---
    # A_INV_EATDRINK opens a combined food+drink menu in inventory order.
    # We enumerate food+drink items preserving their relative inventory position
    # so the cursor index in eatdrink:N matches the menu position correctly.
    def _consumables(s: "GameState") -> list[tuple[str, str]]:
        """Return (action, desc) pairs for all food+drink items in inventory order."""
        out = []
        for i, it in enumerate(s.inventory):
            if it.is_food:
                out.append((f"eat_{i}", f"eat {it.name}"))
            elif it.is_drink:
                out.append((f"drink_{i}", f"drink {it.name}"))
        return out

    def _make_consume(a, s, c):
        # action is eat_N or drink_N where N is the inventory index
        idx = int(a.split("_", 1)[1])
        item = s.inventory[idx] if idx < len(s.inventory) else None
        label = item.name if item else f"item {idx}"
        verb = "eat" if a.startswith("eat_") else "drink"
        # Compute cursor position: count food+drink items before this one
        cursor = sum(1 for it in s.inventory[:idx] if it.is_food or it.is_drink)
        steps = [_MenuStep(action=f"eatdrink:{cursor}")]
        return Dispatch(ActionKind.SKILL, a,
                        skill=MenuSkill(c, steps, label=a, outcome=f"{verb} {label}"))

    specs.append(ActionSpec(
        name="eatdrink", kind=ActionKind.SKILL, group="other",
        available=lambda s: _normal_play(s) and bool(_consumables(s)),
        enumerate_fn=_consumables,
        matches=lambda a: a.startswith("eat_") or a.startswith("drink_"),
        make=_make_consume,
    ))
    specs.append(ActionSpec(
        name="escape", kind=ActionKind.KEY, group="other",
        available=lambda s: True,
        enumerate_fn=lambda s: [("escape", "leave current menu/mode")],
        matches=lambda a: a == "escape",
        make=_key_dispatch("escape", "LEAVESCREEN"),
    ))
    specs.append(ActionSpec(
        name="stop_travel", kind=ActionKind.KEY, group="travel",
        available=lambda s: s.fast_travel_active,
        enumerate_fn=lambda s: [("stop_travel", "exit fast travel")],
        matches=lambda a: a == "stop_travel",
        make=_key_dispatch("stop_travel", "travel_exit"),
    ))

    # --- item menu skills ---
    def make_item_skill(verb: str, key: str):
        def _make(a, s, c):
            idx = int(a.split("_", 1)[1])
            steps = [_MenuStep(action=f"{verb}:{idx}")]
            return Dispatch(ActionKind.SKILL, a,
                            skill=MenuSkill(c, steps, label=a, outcome=f"{verb} item {idx}"))
        return _make

    specs.append(ActionSpec(
        name="pickup", kind=ActionKind.SKILL, group="item",
        available=lambda s: _normal_play(s) and bool(s.floor_items),
        enumerate_fn=lambda s: [(f"pickup_{i}", f"pick up {fi.name}") for i, fi in enumerate(s.floor_items)],
        matches=lambda a: a.startswith("pickup_"),
        make=make_item_skill("pickup", "A_GROUND"),
    ))

    def _hauled(s):
        return [it for it in s.inventory if it.mode == "Hauled"]

    specs.append(ActionSpec(
        name="drop", kind=ActionKind.SKILL, group="item",
        available=lambda s: _normal_play(s) and bool(_hauled(s)),
        enumerate_fn=lambda s: [(f"drop_{i}", f"drop {hi.name}") for i, hi in enumerate(_hauled(s))],
        matches=lambda a: a.startswith("drop_"),
        make=make_item_skill("drop", "A_INV_DROP"),
    ))
    specs.append(ActionSpec(
        name="wield", kind=ActionKind.SKILL, group="item",
        available=lambda s: _normal_play(s) and bool(_hauled(s)),
        enumerate_fn=lambda s: [(f"wield_{i}", f"wield {hi.name}") for i, hi in enumerate(_hauled(s))],
        matches=lambda a: a.startswith("wield_"),
        make=make_item_skill("wield", "A_INV_DRAW_WEAPON"),
    ))

    def _worn(s: "GameState"):
        return [it for it in s.inventory if it.mode == "Worn"]

    def _unworn_armor(s: "GameState"):
        return [it for it in s.inventory if it.mode == "Hauled"
                and not it.is_food and not it.is_drink]

    def _make_inv_action(prefix: str, lua_cmd: str, mode_filter: str | None = None):
        """Make a MenuSkill that opens a filtered inventory menu at the right cursor pos.

        Cursor position is the item's rank within inventory items that match the
        menu's filter (i.e. how many CURSOR_DOWN presses needed).
        """
        def _make(a, s, c):
            # action is <prefix>_<inventory_idx>
            inv_idx = int(a.split("_", 1)[1])
            item = s.inventory[inv_idx] if inv_idx < len(s.inventory) else None
            label = item.name if item else f"item {inv_idx}"
            # Count items before this one that pass the same mode filter
            if mode_filter == "Hauled_noFood":
                cursor = sum(1 for it in s.inventory[:inv_idx]
                             if it.mode == "Hauled" and not it.is_food and not it.is_drink)
            elif mode_filter:
                cursor = sum(1 for it in s.inventory[:inv_idx] if it.mode == mode_filter)
            else:
                cursor = inv_idx
            steps = [_MenuStep(action=f"{lua_cmd}:{cursor}")]
            return Dispatch(ActionKind.SKILL, a,
                            skill=MenuSkill(c, steps, label=a, outcome=f"{prefix} {label}"))
        return _make

    specs.append(ActionSpec(
        name="wear", kind=ActionKind.SKILL, group="item",
        available=lambda s: _normal_play(s) and bool(_unworn_armor(s)),
        enumerate_fn=lambda s: [(f"wear_{i}", f"wear {it.name}")
                                for i, it in enumerate(s.inventory)
                                if it.mode == "Hauled" and not it.is_food and not it.is_drink],
        matches=lambda a: a.startswith("wear_"),
        make=_make_inv_action("wear", "wear", "Hauled_noFood"),
    ))
    specs.append(ActionSpec(
        name="remove_armor", kind=ActionKind.SKILL, group="item",
        available=lambda s: _normal_play(s) and bool(_worn(s)),
        enumerate_fn=lambda s: [(f"remove_{i}", f"remove {it.name}")
                                for i, it in enumerate(s.inventory) if it.mode == "Worn"],
        matches=lambda a: a.startswith("remove_"),
        make=_make_inv_action("remove", "remove", "Worn"),
    ))

    # --- L3 escape hatch: press a raw key or read the current screen ---
    # Blocklisted key patterns — never simulate these (destructive / quit / save)
    _BLOCKED_KEY_PATS = ("QUIT", "RETIRE", "ABANDON", "FORTRESS", "SAVE_GAME",
                         "LEAVESCREEN_ALL", "MAIN_MENU")

    def _validate_press_key(key: str) -> str | None:
        """Return None if the key is allowed, else a reason string."""
        ku = key.upper()
        for pat in _BLOCKED_KEY_PATS:
            if pat in ku:
                return f"blocked key pattern '{pat}'"
        if not key.replace("_", "").isalnum():
            return "key contains invalid characters"
        return None

    specs.append(ActionSpec(
        name="press", kind=ActionKind.KEY, group="other",
        available=lambda s: True,
        enumerate_fn=lambda s: [("press:<KEY>",
                                 "send raw interface key (L3 escape hatch — for unmodeled screens; "
                                 "e.g. press:SELECT, press:LEAVESCREEN, press:A_ATTACK)")],
        matches=lambda a: a.startswith("press:"),
        make=lambda a, s, c: (
            Dispatch(ActionKind.KEY, a,
                     key=a,  # act.lua strips the prefix
                     error=_validate_press_key(a[6:]))
            if _validate_press_key(a[6:]) is None
            else Dispatch(ActionKind.KEY, a,
                          key="A_MOVE_SAME_SQUARE",
                          error=_validate_press_key(a[6:]))
        ),
    ))
    specs.append(ActionSpec(
        name="read_screen", kind=ActionKind.KEY, group="other",
        available=lambda s: True,
        enumerate_fn=lambda s: [("read_screen",
                                 "read current screen text and focus (use before press: on unknown screens)")],
        matches=lambda a: a == "read_screen",
        make=lambda a, s, c: Dispatch(ActionKind.KEY, "read_screen", key="read_screen"),
    ))

    # --- quest log ---
    specs.append(ActionSpec(
        name="read_quest_log", kind=ActionKind.SKILL, group="other",
        available=_normal_play,
        enumerate_fn=lambda s: [("read_quest_log", "open and read the adventure/quest log")],
        matches=lambda a: a == "read_quest_log",
        make=lambda a, s, c: Dispatch(ActionKind.SKILL, a, skill=QuestLogSkill(c)),
    ))

    # --- conversation choices (context) ---
    specs.append(ActionSpec(
        name="conversation", kind=ActionKind.CONTEXT, group="conversation",
        available=_in_conversation,
        enumerate_fn=lambda s: [(f"conversation_{c.index}", c.text) for c in s.conversation_choices],
        matches=lambda a: a.startswith("conversation_"),
        make=lambda a, s, c: Dispatch(ActionKind.CONTEXT, a, conv_index=int(a.split("_", 1)[1])),
    ))

    return ActionRegistry(specs)
