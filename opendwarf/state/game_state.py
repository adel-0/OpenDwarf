"""Structured game state parsed from DFHack Lua output."""

from __future__ import annotations

from dataclasses import dataclass, field

from opendwarf.spatial.compass import sign

# --- summary() block caps (6.1: keep the per-turn context bounded) ---
# Every list block in summary() is capped so a large registry / a 98-item
# "Ask for directions" menu can never blow up the per-turn token budget.
# Overflowed blocks print a "(… N more)" tail so the LLM knows it's truncated.
_CAP_SKILLS = 8
_CAP_WOUNDS = 6
_CAP_COMBAT_LOG = 5
_CAP_ANNOUNCE = 6
_CAP_PARTY = 6
_CAP_NEARBY = 5
_CAP_FLOOR_ITEMS = 6
_CAP_HAULED = 5
_CAP_FACTIONS = 5
_CAP_RELATIONSHIPS = 5
_CAP_QUESTS = 6
_CAP_CHOICES = 25
_CAP_SITES = 8


def _capped(items: list, cap: int) -> tuple[list, int]:
    """Return (first `cap` items, count of dropped items)."""
    return items[:cap], max(0, len(items) - cap)


@dataclass
class Position:
    x: int
    y: int
    z: int

    def __str__(self) -> str:
        return f"({self.x}, {self.y}, {self.z})"


@dataclass
class UnitInfo:
    id: int
    name: str
    race: str
    position: Position
    is_hostile: bool
    distance: int  # Manhattan distance from adventurer
    hist_fig_id: int = -1  # Historical figure ID (-1 = non-historic)
    is_tame: bool = False     # a domesticated/pet creature — never a combat target
    is_citizen: bool = False  # member of a civ — attacking is a crime, not a hunt

    def __str__(self) -> str:
        hostile = " [HOSTILE]" if self.is_hostile else ""
        return f"{self.name} ({self.race}){hostile} at {self.position} dist={self.distance}"


@dataclass
class InventoryItem:
    name: str
    mode: str  # Hauled, Worn, Weapon, etc.
    quality: str = "ordinary"
    is_food: bool = False
    is_drink: bool = False

    def __str__(self) -> str:
        q = f" ({self.quality})" if self.quality and self.quality != "ordinary" else ""
        return f"{self.name}{q} [{self.mode}]"


@dataclass
class Skill:
    id: str
    level: int
    experience: int = 0

    def __str__(self) -> str:
        return f"{self.id} lv{self.level}"


@dataclass
class Wound:
    part: str
    status: str

    def __str__(self) -> str:
        return f"{self.part}: {self.status}"


@dataclass
class PartyMember:
    hf_id: int
    name: str


@dataclass
class ConversationChoice:
    index: int
    text: str


@dataclass
class EntityLink:
    name: str
    link_type: str  # MEMBER, POSITION, FORMER_MEMBER, etc.

    def __str__(self) -> str:
        return f"{self.name} ({self.link_type})" if self.name else self.link_type


@dataclass
class NPCRelationship:
    name: str
    unit_id: int
    relationship: str  # FRIEND, SPOUSE, ENEMY, KNOWN, etc.

    def __str__(self) -> str:
        return f"{self.name} [{self.relationship}]"


@dataclass
class NearbySite:
    id: int
    name: str
    site_type: str
    distance: int  # embark tiles
    direction: str  # compass direction from player
    world_x: int | None = None  # embark-tile centre (global coords), when known
    world_y: int | None = None


@dataclass
class GameState:
    # Physiological thresholds — runtime-empirical, not derivable from df-structures.
    # The schema confirms the FIELD PATHS (unit.counters2.*) but not the numeric cutoffs.
    # These values come from community wiki data for v50+; needs live capture to verify.
    _HUNGRY: int = field(default=75_000, init=False, repr=False)
    _THIRSTY: int = field(default=50_000, init=False, repr=False)
    _DROWSY: int = field(default=57_600, init=False, repr=False)
    _HUNGRY_CRITICAL: int = field(default=150_000, init=False, repr=False)
    _THIRSTY_CRITICAL: int = field(default=100_000, init=False, repr=False)
    _DROWSY_CRITICAL: int = field(default=115_200, init=False, repr=False)

    # Adventurer
    adventurer_name: str = ""
    adventurer_position: Position | None = None
    # Absolute world-tile position (region offset + local). The local map window
    # re-centers as the adventurer travels, so the local->absolute offset is not
    # stable between map fetches — this lets the extractor recompute it fresh.
    adventurer_abs_position: Position | None = None
    blood_count: int = 0
    blood_max: int = 0

    # Physiological timers (count up; -1 = unknown)
    hunger_timer: int = -1
    thirst_timer: int = -1
    sleepiness_timer: int = -1
    exhaustion: int = -1

    # Game state
    tick_counter: int = 0
    total_move: int = -1  # cumulative successful moves (-1 = unavailable)
    player_control_state: str = ""
    menu_state: str = ""
    focus_state: str = ""
    message: str = ""
    is_adventure_mode: bool = False
    sneaking: bool = False  # stealth mode active (flags1.hidden_in_ambush)

    # World context
    world_name: str = ""
    region_name: str = ""
    site_name: str = ""
    site_type: str = ""
    # Adventurer position in embark-tile (global) coords; -1 = unknown.
    # During fast travel this is army_pos // 3; otherwise region offset + local//16.
    player_world_x: int = -1
    player_world_y: int = -1

    # Adventurer skills
    skills: list[Skill] = field(default_factory=list)

    # World
    nearby_units: list[UnitInfo] = field(default_factory=list)
    hostile_units: list[UnitInfo] = field(default_factory=list)
    inventory: list[InventoryItem] = field(default_factory=list)
    conversation_choices: list[ConversationChoice] = field(default_factory=list)
    conversation_phase: str = "none"  # "none", "select_npc", "dialogue"

    # Adventure attack menu (dungeonmode/Attack) — driven by CombatStrikeSkill.
    # Schema: adventure_interface_attack_mode_type (df.d_interface.xml:5267)
    #   NONE=-1, UNIT_CHOICE=0, CONFIRM=1, MOVE_CHOICE=2, AIM_TARGET=3, AIM_ATTACK=4
    # The skill drives: mode 0 (pick target) → mode 2 (pick move/Strike) →
    #   mode 3 (pick body part) → mode 4 (pick weapon) → resolves.
    attack_menu_open: bool = False
    attack_menu_mode: int = -1  # schema-confirmed: UNIT_CHOICE=0, MOVE_CHOICE=2, AIM_TARGET=3, AIM_ATTACK=4
    attack_unit_choice: list[int] = field(default_factory=list)  # target ids, screen-row order

    # Body
    wounds: list[Wound] = field(default_factory=list)

    # Map
    map_tiles: list[str] = field(default_factory=list)  # 5x5 grid rows

    # Floor items at adventurer's position
    floor_items: list[InventoryItem] = field(default_factory=list)

    # Party
    party: list[PartyMember] = field(default_factory=list)

    # Announcements (NPC speech, event text)
    showing_announcements: bool = False
    # True only for the full-screen dungeonmode/Announcements viewscreen (deity
    # quest pulls / event text), which is dismissed with LEAVESCREEN, not SELECT.
    announcement_screen: bool = False
    announcement_text: list[str] = field(default_factory=list)
    # Centered "mega" popups (status.popups) — world/agreement notices that
    # wedge ALL input until drained. Deduped texts; empty when none pending.
    popup_messages: list[str] = field(default_factory=list)

    # Combat
    in_combat: bool = False
    combat_log: list[str] = field(default_factory=list)

    # Reputation & relationships
    adventurer_entities: list[EntityLink] = field(default_factory=list)
    npc_relationships: list[NPCRelationship] = field(default_factory=list)

    # Quests
    quests: list[str] = field(default_factory=list)

    # Fast travel
    fast_travel_active: bool = False
    fast_travel_army_pos: Position | None = None  # world-coord position during fast travel

    # Nearby sites (for LLM context)
    nearby_sites: list[NearbySite] = field(default_factory=list)

    # Death detection — set by from_raw() when the adventurer unit is dead.
    # Primary signal: adventurer.flags2.killed (schema: unit_flags2.HAS_BEEN_KILLED)
    #   or flags1.inactive (schema: unit_flags1.DEAD) or not isAlive.
    # Fallback: is_adventure_mode=False while we were previously in adventure mode.
    # Focus-string signal: runtime-empirical — no death viewscreen type in schema.
    #   df-structures v53.10 has no viewscreen_adventure_endst; needs live capture.
    adventurer_dead: bool = False



    @staticmethod
    def _compass(dx: int, dy: int) -> str:
        """Return compass direction string. DF: +x=East, +y=South."""
        if dx == 0 and dy == 0:
            return "here"
        angle_map = [
            ((-1, -1), "NW"), ((0, -1), "N"), ((1, -1), "NE"),
            ((1, 0), "E"), ((1, 1), "SE"), ((0, 1), "S"),
            ((-1, 1), "SW"), ((-1, 0), "W"),
        ]
        sx, sy = sign(dx, dy)
        # Prefer diagonal when both components are similar magnitude
        if abs(dx) > 0 and abs(dy) > 0 and abs(dx) * 2 >= abs(dy) and abs(dy) * 2 >= abs(dx):
            key = (sx, sy)
        elif abs(dx) > abs(dy):
            key = (sx, 0)
        elif abs(dy) > abs(dx):
            key = (0, sy)
        else:
            key = (sx, sy)
        return dict(angle_map).get(key, "?")

    @staticmethod
    def from_raw(data: dict) -> GameState:
        """Parse JSON output from opendwarf--state.lua into a GameState."""
        state = GameState()

        # Adventurer (can be empty list [] when in fast travel mode)
        adv = data.get("adventurer", {})
        if isinstance(adv, list):
            adv = {}  # empty table from Lua encodes as []
        state.adventurer_name = adv.get("name", "Unknown")
        pos = adv.get("position", {})
        if pos:
            state.adventurer_position = Position(pos.get("x", 0), pos.get("y", 0), pos.get("z", 0))
        abs_pos = adv.get("abs_position", {})
        if abs_pos:
            state.adventurer_abs_position = Position(
                abs_pos.get("x", 0), abs_pos.get("y", 0), abs_pos.get("z", 0))
        state.blood_count = adv.get("blood_count", 0)
        state.blood_max = adv.get("blood_max", 0)
        state.sneaking = bool(adv.get("sneaking", False))

        # Physiological timers
        state.hunger_timer = adv.get("hunger_timer", -1)
        state.thirst_timer = adv.get("thirst_timer", -1)
        state.sleepiness_timer = adv.get("sleepiness_timer", -1)
        state.exhaustion = adv.get("exhaustion", -1)

        # Skills
        for s in adv.get("skills", []):
            state.skills.append(Skill(
                id=s.get("id", "?"),
                level=s.get("level", 0),
                experience=s.get("experience", 0),
            ))

        # Game state
        game = data.get("game", {})
        state.tick_counter = game.get("tick_counter", 0)
        state.total_move = game.get("total_move", -1)
        state.player_control_state = game.get("player_control_state", "")
        state.menu_state = game.get("menu_state", "")
        state.focus_state = game.get("focus_state", "")
        state.message = game.get("message", "")
        state.is_adventure_mode = game.get("is_adventure_mode", False)

        # World context (Lua empty table encodes as [] when nothing was set)
        world = data.get("world", {})
        if isinstance(world, dict):
            state.world_name = world.get("world_name", "")
            state.region_name = world.get("region_name", "")
            state.site_name = world.get("site_name", "")
            state.site_type = world.get("site_type", "")
            state.player_world_x = world.get("player_world_x", -1)
            state.player_world_y = world.get("player_world_y", -1)

        # Units
        for u in data.get("nearby_units", []):
            upos = u.get("position", {})
            unit = UnitInfo(
                id=u.get("id", 0),
                name=u.get("name", "?"),
                race=u.get("race", "?"),
                position=Position(upos.get("x", 0), upos.get("y", 0), upos.get("z", 0)),
                is_hostile=u.get("is_hostile", False),
                distance=u.get("distance", 0),
                hist_fig_id=u.get("hist_figure_id", -1),
                is_tame=u.get("is_tame", False),
                is_citizen=u.get("is_citizen", False),
            )
            state.nearby_units.append(unit)
            if unit.is_hostile:
                state.hostile_units.append(unit)

        # Inventory
        for item in data.get("inventory", []):
            state.inventory.append(InventoryItem(
                name=item.get("name", "?"),
                mode=item.get("mode", "?"),
                quality=item.get("quality", "ordinary"),
                is_food=item.get("is_food", False),
                is_drink=item.get("is_drink", False),
            ))

        # Conversation
        state.conversation_phase = data.get("conversation_phase", "none")
        for c in data.get("conversation_choices", []):
            state.conversation_choices.append(ConversationChoice(
                index=c.get("index", 0),
                text=c.get("text", ""),
            ))

        # Attack menu
        am = data.get("attack_menu") or {}
        state.attack_menu_open = am.get("open", False)
        state.attack_menu_mode = am.get("mode", -1)
        state.attack_unit_choice = [int(i) for i in am.get("unit_choice", [])]

        # Wounds
        for w in adv.get("wounds", []):
            state.wounds.append(Wound(part=w.get("part", "?"), status=w.get("status", "?")))

        # Map tiles
        state.map_tiles = data.get("map_tiles", [])

        # Floor items
        for item in data.get("floor_items", []):
            state.floor_items.append(InventoryItem(
                name=item.get("name", "?"),
                mode="Floor",
                quality=item.get("quality", "ordinary"),
            ))

        # Party
        for p in data.get("party", []):
            state.party.append(PartyMember(hf_id=p.get("hf_id", 0), name=p.get("name", "?")))

        # Announcements
        state.showing_announcements = data.get("showing_announcements", False)
        state.announcement_screen = data.get("announcement_screen", False)
        state.announcement_text = data.get("announcement_text", [])
        state.popup_messages = data.get("popup_messages", [])

        # Combat
        state.in_combat = data.get("in_combat", False)
        state.combat_log = data.get("combat_log", [])

        # Entity/faction links
        for e in data.get("adventurer_entities", []):
            state.adventurer_entities.append(EntityLink(
                name=e.get("name", ""),
                link_type=e.get("link_type", "MEMBER"),
            ))

        # NPC relationships
        for r in data.get("npc_relationships", []):
            state.npc_relationships.append(NPCRelationship(
                name=r.get("name", "?"),
                unit_id=r.get("unit_id", 0),
                relationship=r.get("relationship", "KNOWN"),
            ))

        # Quests
        state.quests = data.get("quests", [])

        # Fast travel
        ft = data.get("fast_travel", {})
        if isinstance(ft, dict):
            state.fast_travel_active = ft.get("active", False)
            army_pos = ft.get("army_pos")
            if isinstance(army_pos, dict):
                state.fast_travel_army_pos = Position(
                    army_pos.get("x", 0), army_pos.get("y", 0), army_pos.get("z", 0)
                )

        # Nearby sites
        for s in data.get("nearby_sites", []):
            state.nearby_sites.append(NearbySite(
                id=s.get("id", 0),
                name=s.get("name", "?"),
                site_type=s.get("type", "?"),
                distance=s.get("distance", 0),
                direction=s.get("direction", "?"),
                world_x=s.get("world_x"),
                world_y=s.get("world_y"),
            ))

        # Death detection.
        # Three independent signals set this flag:
        #   1. Lua extractor reports "adventurer_dead": true (flags2.killed or not isAlive).
        #   2. Adventure mode has ended (is_adventure_mode=False) while an adventurer
        #      was previously known — this catches the case where the death screen
        #      transitions back to the title.
        #   3. Focus string matches the death/end screen pattern (LIVE-VERIFY pending;
        #      likely "dungeonmode/Default" briefly then "title" or non-dungeon focus).
        state.adventurer_dead = bool(data.get("adventurer_dead", False))
        # Signal 3: focus string explicitly signals end-of-adventure.
        # Schema audit (df.d_interface.xml): no "viewscreen_adventure_endst" or
        # equivalent death/end viewscreen type exists in df-structures v53.10.
        # The schema lists viewscreen_dungeonmodest, viewscreen_setupadventurest,
        # viewscreen_titlest — none named for death/end. The focus string on the
        # death screen is runtime-empirical (needs live capture of a real death).
        # Candidates below are guesses kept as a net; they will match only if
        # the runtime focus happens to contain these substrings.
        death_focus_patterns = ("dungeonmode/end", "adventure_over")
        if state.focus_state and any(p in state.focus_state.lower() for p in death_focus_patterns):
            state.adventurer_dead = True

        return state

    @property
    def taking_input(self) -> bool:
        return self.player_control_state == "TAKING_INPUT"

    @property
    def health_pct(self) -> int:
        if self.blood_max <= 0:
            return 100
        return int(self.blood_count / self.blood_max * 100)

    @property
    def huntable_units(self) -> list["UnitInfo"]:
        """Units the adventurer may legitimately attack — the superset of
        `hostile_units` (active dangers) plus wild creatures that DFHack's
        `isDanger` never flags until provoked (wolves, deer, …).

        A wild creature is identified as non-historic (`hist_fig_id < 0`),
        not tame (so not a pet), and not a civ citizen (attacking those is a
        crime, not a hunt). Named figures (`hist_fig_id >= 0`) are huntable
        only if already hostile, so quest-givers and townsfolk are spared.
        Combat target selection (the `attack` action, `GrindCombatBehavior`)
        keys on this; danger/flee/in_combat semantics still key on
        `hostile_units` alone so passive wildlife never triggers a flee.
        """
        out: list["UnitInfo"] = []
        for u in self.nearby_units:
            if u.is_hostile:
                out.append(u)
            elif u.hist_fig_id < 0 and not u.is_tame and not u.is_citizen:
                out.append(u)
        return out

    @property
    def hungry(self) -> bool:
        return self.hunger_timer >= self._HUNGRY

    @property
    def hungry_critical(self) -> bool:
        return self.hunger_timer >= self._HUNGRY_CRITICAL

    @property
    def thirsty(self) -> bool:
        return self.thirst_timer >= self._THIRSTY

    @property
    def thirsty_critical(self) -> bool:
        return self.thirst_timer >= self._THIRSTY_CRITICAL

    @property
    def drowsy(self) -> bool:
        return self.sleepiness_timer >= self._DROWSY

    @property
    def drowsy_critical(self) -> bool:
        return self.sleepiness_timer >= self._DROWSY_CRITICAL

    @property
    def exhaustion_critical(self) -> bool:
        # Schema confirms the field path: unit.counters2.exhaustion (df.unit.xml:2940,
        # original-name='exertion', uint32_t). The threshold 2000 is runtime-empirical
        # (not in the schema) — needs live capture against the in-game exhaustion icon.
        return self.exhaustion >= 2000

    def _mode(self) -> str:
        """Coarse context mode driving which heavy blocks summary() shows.

        combat → threats + map core; conversation → dialogue + relationships;
        exploration (default) → map + sites + inventory. Keeps the per-turn
        context focused on what the current situation needs (6.1)."""
        if self.in_combat or self.hostile_units:
            return "combat"
        if self.conversation_choices:
            return "conversation"
        return "exploration"

    def summary(self) -> str:
        """Concise, situational, bounded text summary for the LLM context window.

        Blocks are selected by `_mode()` so a fight isn't buried under faction /
        site / quest noise and a conversation isn't buried under the world map's
        site list; every list block is capped (see the _CAP_* constants) so the
        per-turn token cost can't grow unboundedly."""
        mode = self._mode()
        lines = []
        lines.append(f"=== {self.adventurer_name} ===")
        if self.site_name:
            loc = self.site_name
            if self.site_type:
                loc += f" ({self.site_type})"
            lines.append(f"Location: {loc}")
        elif self.region_name:
            lines.append(f"Location: {self.region_name}")
        lines.append(f"Position: {self.adventurer_position}")
        lines.append(f"Health: {self.health_pct}% ({self.blood_count}/{self.blood_max})")
        physio: list[str] = []
        if self.hungry_critical:
            physio.append("STARVING")
        elif self.hungry:
            physio.append("hungry")
        if self.thirsty_critical:
            physio.append("DEHYDRATED")
        elif self.thirsty:
            physio.append("thirsty")
        if self.drowsy_critical:
            physio.append("EXHAUSTED")
        elif self.drowsy:
            physio.append("drowsy")
        if self.exhaustion_critical:
            physio.append("FATIGUE-CRITICAL")
        if self.sneaking:
            physio.append("SNEAKING (hidden)")
        if physio:
            lines.append(f"Status: {', '.join(physio)}")
        lines.append(f"Tick: {self.tick_counter} | State: {self.player_control_state} | Menu: {self.menu_state}")
        lines.append(f"Focus: {self.focus_state}")
        if self.message:
            lines.append(f"Message: {self.message}")

        if self.skills:
            top_skills = sorted(self.skills, key=lambda s: s.level, reverse=True)[:_CAP_SKILLS]
            lines.append(f"\n-- Skills -- {', '.join(str(s) for s in top_skills)}")

        if self.wounds:
            shown, more = _capped(self.wounds, _CAP_WOUNDS)
            lines.append("\n-- Wounds --")
            for w in shown:
                lines.append(f"  {w}")
            if more:
                lines.append(f"  (… {more} more)")

        if self.map_tiles:
            lines.append("\n-- Map (@ = you, . floor, # wall, + door, < > stairs, ^ ramp, "
                         "~ water, u/h units, ? unexplored) --")
            has_marker = any("@" in row for row in self.map_tiles)
            for i, row in enumerate(self.map_tiles):
                # Fallback only for narrow views with no overlay (@ already placed by extractor)
                if not has_marker and i == len(self.map_tiles) // 2:
                    mid = len(row) // 2
                    row = row[:mid] + "@" + row[mid + 1:]
                lines.append(f"  {row}")

        if self.party:
            shown, more = _capped(self.party, _CAP_PARTY)
            lines.append(f"\n-- Party ({len(self.party)}) --")
            for p in shown:
                lines.append(f"  {p.name}")
            if more:
                lines.append(f"  (… {more} more)")

        if self.showing_announcements:
            shown, more = _capped(self.announcement_text, _CAP_ANNOUNCE)
            lines.append("\n-- NPC Speaking (press select to continue) --")
            for t in shown:
                lines.append(f"  {t}")
            if more:
                lines.append(f"  (… {more} more)")

        if self.in_combat:
            lines.append("\n-- COMBAT --")
            for log in self.combat_log[-_CAP_COMBAT_LOG:]:
                lines.append(f"  {log}")

        if self.hostile_units:
            lines.append(f"\n-- Hostile Units ({len(self.hostile_units)}) --")
            for u in self.hostile_units:
                if self.adventurer_position and u.position:
                    dx = u.position.x - self.adventurer_position.x
                    dy = u.position.y - self.adventurer_position.y
                    direction = self._compass(dx, dy)
                    lines.append(f"  {u.name} ({u.race}) [HOSTILE, {direction}, dist={u.distance}]")
                else:
                    lines.append(f"  {u}")

        # Friendly bystanders are noise mid-fight — show them only when not in combat.
        if self.nearby_units and mode != "combat":
            friendly = [u for u in self.nearby_units if not u.is_hostile]
            if friendly:
                shown, more = _capped(friendly, _CAP_NEARBY)
                lines.append(f"\n-- Nearby ({len(friendly)}) --")
                for u in shown:
                    if self.adventurer_position and u.position:
                        dx = u.position.x - self.adventurer_position.x
                        dy = u.position.y - self.adventurer_position.y
                        direction = self._compass(dx, dy)
                        lines.append(f"  {u.name} ({u.race}) [{direction}, dist={u.distance}]")
                    else:
                        lines.append(f"  {u}")
                if more:
                    lines.append(f"  (… {more} more)")

        if self.floor_items and mode == "exploration":
            shown, more = _capped(self.floor_items, _CAP_FLOOR_ITEMS)
            lines.append(f"\n-- Floor Items (use pickup_N to grab) --")
            for i, item in enumerate(shown):
                q = f" ({item.quality})" if item.quality and item.quality != "ordinary" else ""
                lines.append(f"  [{i}] {item.name}{q}")
            if more:
                lines.append(f"  (… {more} more)")

        # Inventory is irrelevant mid-conversation; in combat only weapons matter.
        if self.inventory and mode != "conversation":
            lines.append(f"\n-- Inventory ({len(self.inventory)}) --")
            weapons = [i for i in self.inventory if i.mode == "Weapon"]
            if weapons:
                lines.append(f"  Weapons: {', '.join(str(w) for w in weapons)}")
            if mode != "combat":
                worn = [i for i in self.inventory if i.mode == "Worn"]
                hauled = list(enumerate([i for i in self.inventory if i.mode == "Hauled"]))
                if worn:
                    lines.append(f"  Worn: {', '.join(str(w) for w in worn[:5])}")
                if hauled:
                    hauled_strs = [f"[{i}] {item}" for i, item in hauled[:_CAP_HAULED]]
                    lines.append(f"  Hauled: {', '.join(hauled_strs)}")
                # Show food/drink with their inventory index (eat_N / drink_N use inventory idx)
                food_inv = [(i, it) for i, it in enumerate(self.inventory) if it.is_food]
                drink_inv = [(i, it) for i, it in enumerate(self.inventory) if it.is_drink]
                if food_inv:
                    food_strs = [f"eat_{i}: {it.name}" for i, it in food_inv[:4]]
                    lines.append(f"  Food: {', '.join(food_strs)}")
                if drink_inv:
                    drink_strs = [f"drink_{i}: {it.name}" for i, it in drink_inv[:4]]
                    lines.append(f"  Drink: {', '.join(drink_strs)}")

        # Faction membership is background — only worth the tokens when exploring.
        if self.adventurer_entities and mode == "exploration":
            shown, more = _capped(self.adventurer_entities, _CAP_FACTIONS)
            lines.append(f"\n-- Factions --")
            for e in shown:
                lines.append(f"  {e}")
            if more:
                lines.append(f"  (… {more} more)")

        if self.npc_relationships and mode != "combat":
            shown, more = _capped(self.npc_relationships, _CAP_RELATIONSHIPS)
            lines.append(f"\n-- Known NPCs nearby --")
            for r in shown:
                lines.append(f"  {r}")
            if more:
                lines.append(f"  (… {more} more)")

        if self.quests and mode != "combat":
            shown, more = _capped(self.quests, _CAP_QUESTS)
            lines.append(f"\n-- Quests --")
            for q in shown:
                lines.append(f"  {q}")
            if more:
                lines.append(f"  (… {more} more)")

        if self.conversation_choices:
            phase_label = {
                "select_npc": "Select NPC to address",
                "dialogue": "Dialogue choices",
            }.get(self.conversation_phase, "Conversation")
            shown, more = _capped(self.conversation_choices, _CAP_CHOICES)
            lines.append(f"\n-- Conversation ({phase_label}) --")
            for c in shown:
                lines.append(f"  [{c.index}] {c.text}")
            if more:
                lines.append(f"  (… {more} more choices — scroll/ask to narrow)")

        # The world's site list is noise mid-fight or mid-conversation.
        if self.nearby_sites and mode == "exploration":
            shown, more = _capped(self.nearby_sites, _CAP_SITES)
            lines.append(f"\n-- Nearby Sites --")
            for s in shown:
                label = f"{s.name} ({s.site_type})"
                if s.distance == 0 or (self.site_name and s.name == self.site_name):
                    lines.append(f"  {label} [YOU ARE HERE]")
                else:
                    lines.append(f"  {label} — {s.distance} tiles {s.direction}")
            if more:
                lines.append(f"  (… {more} more)")

        if self.fast_travel_active:
            lines.append("\n-- FAST TRAVEL MODE ACTIVE --")
            if self.fast_travel_army_pos:
                lines.append(f"  World position: {self.fast_travel_army_pos}")
            lines.append("  Use move_n/s/e/w/ne/nw/se/sw to travel long distances.")
            lines.append("  Use stop_travel to exit fast travel when you reach a site (distance=0).")

        return "\n".join(lines)
