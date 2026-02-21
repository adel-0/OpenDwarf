"""Structured game state parsed from DFHack Lua output."""

from __future__ import annotations

from dataclasses import dataclass, field


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

    def __str__(self) -> str:
        hostile = " [HOSTILE]" if self.is_hostile else ""
        return f"{self.name} ({self.race}){hostile} at {self.position} dist={self.distance}"


@dataclass
class InventoryItem:
    name: str
    mode: str  # Hauled, Worn, Weapon, etc.
    quality: str = "ordinary"

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
class GameState:
    # Adventurer
    adventurer_name: str = ""
    adventurer_position: Position | None = None
    blood_count: int = 0
    blood_max: int = 0

    # Game state
    tick_counter: int = 0
    player_control_state: str = ""
    menu_state: str = ""
    focus_state: str = ""
    message: str = ""
    is_adventure_mode: bool = False

    # World context
    world_name: str = ""
    region_name: str = ""
    site_name: str = ""
    site_type: str = ""

    # Adventurer skills
    skills: list[Skill] = field(default_factory=list)

    # World
    nearby_units: list[UnitInfo] = field(default_factory=list)
    hostile_units: list[UnitInfo] = field(default_factory=list)
    inventory: list[InventoryItem] = field(default_factory=list)
    conversation_choices: list[ConversationChoice] = field(default_factory=list)
    conversation_phase: str = "none"  # "none", "select_npc", "dialogue"

    # Body
    wounds: list[Wound] = field(default_factory=list)

    # Map
    map_tiles: list[str] = field(default_factory=list)  # 5x5 grid rows

    # Party
    party: list[PartyMember] = field(default_factory=list)

    # Announcements (NPC speech, event text)
    showing_announcements: bool = False
    announcement_text: list[str] = field(default_factory=list)

    # Combat
    in_combat: bool = False
    combat_log: list[str] = field(default_factory=list)



    @staticmethod
    def from_raw(data: dict) -> GameState:
        """Parse JSON output from opendwarf--state.lua into a GameState."""
        state = GameState()

        # Adventurer
        adv = data.get("adventurer", {})
        state.adventurer_name = adv.get("name", "Unknown")
        pos = adv.get("position", {})
        if pos:
            state.adventurer_position = Position(pos.get("x", 0), pos.get("y", 0), pos.get("z", 0))
        state.blood_count = adv.get("blood_count", 0)
        state.blood_max = adv.get("blood_max", 0)

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
            ))

        # Conversation
        state.conversation_phase = data.get("conversation_phase", "none")
        for c in data.get("conversation_choices", []):
            state.conversation_choices.append(ConversationChoice(
                index=c.get("index", 0),
                text=c.get("text", ""),
            ))

        # Wounds
        for w in adv.get("wounds", []):
            state.wounds.append(Wound(part=w.get("part", "?"), status=w.get("status", "?")))

        # Map tiles
        state.map_tiles = data.get("map_tiles", [])

        # Party
        for p in data.get("party", []):
            state.party.append(PartyMember(hf_id=p.get("hf_id", 0), name=p.get("name", "?")))

        # Announcements
        state.showing_announcements = data.get("showing_announcements", False)
        state.announcement_text = data.get("announcement_text", [])

        # Combat
        state.in_combat = data.get("in_combat", False)
        state.combat_log = data.get("combat_log", [])

        return state

    @property
    def taking_input(self) -> bool:
        return self.player_control_state == "TAKING_INPUT"

    @property
    def health_pct(self) -> int:
        if self.blood_max <= 0:
            return 100
        return int(self.blood_count / self.blood_max * 100)

    def summary(self) -> str:
        """Concise text summary for the LLM context window."""
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
        lines.append(f"Tick: {self.tick_counter} | State: {self.player_control_state} | Menu: {self.menu_state}")
        lines.append(f"Focus: {self.focus_state}")
        if self.message:
            lines.append(f"Message: {self.message}")

        if self.skills:
            top_skills = sorted(self.skills, key=lambda s: s.level, reverse=True)[:8]
            lines.append(f"\n-- Skills -- {', '.join(str(s) for s in top_skills)}")

        if self.wounds:
            lines.append("\n-- Wounds --")
            for w in self.wounds:
                lines.append(f"  {w}")

        if self.map_tiles:
            lines.append("\n-- Map (5x5, @ = you, . = floor, # = wall) --")
            for i, row in enumerate(self.map_tiles):
                # Mark center tile as @
                if i == len(self.map_tiles) // 2:
                    mid = len(row) // 2
                    row = row[:mid] + "@" + row[mid + 1:]
                lines.append(f"  {row}")

        if self.party:
            lines.append(f"\n-- Party ({len(self.party)}) --")
            for p in self.party:
                lines.append(f"  {p.name}")

        if self.showing_announcements:
            lines.append("\n-- NPC Speaking (press select to continue) --")
            for t in self.announcement_text:
                lines.append(f"  {t}")

        if self.in_combat:
            lines.append("\n-- COMBAT --")
            for log in self.combat_log[-5:]:
                lines.append(f"  {log}")

        if self.hostile_units:
            lines.append(f"\n-- Hostile Units ({len(self.hostile_units)}) --")
            for u in self.hostile_units:
                lines.append(f"  {u}")

        if self.nearby_units:
            friendly = [u for u in self.nearby_units if not u.is_hostile]
            if friendly:
                lines.append(f"\n-- Nearby ({len(friendly)}) --")
                for u in friendly[:5]:
                    lines.append(f"  {u}")

        if self.inventory:
            lines.append(f"\n-- Inventory ({len(self.inventory)}) --")
            weapons = [i for i in self.inventory if i.mode == "Weapon"]
            worn = [i for i in self.inventory if i.mode == "Worn"]
            hauled = [i for i in self.inventory if i.mode == "Hauled"]
            if weapons:
                lines.append(f"  Weapons: {', '.join(str(w) for w in weapons)}")
            if worn:
                lines.append(f"  Worn: {', '.join(str(w) for w in worn[:5])}")
            if hauled:
                lines.append(f"  Hauled: {', '.join(str(h) for h in hauled[:5])}")

        if self.conversation_choices:
            phase_label = {
                "select_npc": "Select NPC to address",
                "dialogue": "Dialogue choices",
            }.get(self.conversation_phase, "Conversation")
            lines.append(f"\n-- Conversation ({phase_label}) --")
            for c in self.conversation_choices:
                lines.append(f"  [{c.index}] {c.text}")

        return "\n".join(lines)
