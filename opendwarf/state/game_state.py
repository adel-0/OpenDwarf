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

    def __str__(self) -> str:
        return f"{self.name} [{self.mode}]"


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

    # World
    nearby_units: list[UnitInfo] = field(default_factory=list)
    hostile_units: list[UnitInfo] = field(default_factory=list)
    inventory: list[InventoryItem] = field(default_factory=list)
    conversation_choices: list[ConversationChoice] = field(default_factory=list)

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

        # Game state
        game = data.get("game", {})
        state.tick_counter = game.get("tick_counter", 0)
        state.player_control_state = game.get("player_control_state", "")
        state.menu_state = game.get("menu_state", "")
        state.focus_state = game.get("focus_state", "")
        state.message = game.get("message", "")
        state.is_adventure_mode = game.get("is_adventure_mode", False)

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
            ))

        # Conversation
        for c in data.get("conversation_choices", []):
            state.conversation_choices.append(ConversationChoice(
                index=c.get("index", 0),
                text=c.get("text", ""),
            ))

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
        lines.append(f"Position: {self.adventurer_position}")
        lines.append(f"Health: {self.health_pct}% ({self.blood_count}/{self.blood_max})")
        lines.append(f"Tick: {self.tick_counter} | State: {self.player_control_state} | Menu: {self.menu_state}")
        lines.append(f"Focus: {self.focus_state}")
        if self.message:
            lines.append(f"Message: {self.message}")

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
            lines.append("\n-- Conversation --")
            for c in self.conversation_choices:
                lines.append(f"  [{c.index}] {c.text}")

        return "\n".join(lines)
